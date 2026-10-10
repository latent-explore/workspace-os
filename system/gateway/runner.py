"""Runner: starts Claude Code headless and returns one standard result."""
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CHAT_PROMPT = (
    "You are talking to the user over a phone chat. Reply in at most 5 short lines, "
    "in plain simple language. Ask one question at a time. If an answer needs more, "
    "write it to a file and give the path."
)
CHAT_TOOLS = "Read,Glob,Grep"  # chat is read-only

ADMIN_PROMPT = (
    "You are the admin assistant for workspace-os, talking over a phone chat in the Reception group. "
    "Reply in at most 5 short lines, in plain language. Make every change with the bin/wos helper and nothing else. "
    "Confirm names and repos with the user before creating anything. Never read or print secrets."
)
ADMIN_TOOLS = "Read,Glob,Grep,Bash(bin/wos:*),Bash(./bin/wos:*)"
SECRET_PATHS = "Read(~/.config/workspace-os/**)"  # never let an agent read the secrets file

TASK_PROMPT = (
    "You are doing a coding task. Work only inside the current folder: it holds one git "
    "worktree per repo, each already on the task branch. Run the tests. Commit your work in "
    "each repo with clear messages. Do not push and do not open pull requests; the system does that. "
    "End with exactly one line: 'SUMMARY: <what you did>', or 'BLOCKED: <what you need from the user>'."
)
TASK_TOOLS = "Read,Glob,Grep,Edit,Write,Bash"
# Defence in depth only. The real wall is the wos Linux user and the systemd sandbox.
TASK_DENY = "Bash(git push:*),Bash(gh:*),Bash(sudo:*),WebFetch,WebSearch," + SECRET_PATHS
SECRET_ENV = ("TELEGRAM_BOT_TOKEN",)


@dataclass
class RunResult:
    status: str  # "ok" | "error"
    text: str
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict = field(default_factory=dict)


class ClaudeRunner:
    def __init__(self, claude_bin: str = "claude", model: str | None = None, timeout: int = 600,
                 task_timeout: int = 1800):
        self.claude_bin = claude_bin
        self.model = model
        self.timeout = timeout
        self.task_timeout = task_timeout

    def run(self, prompt: str, cwd: Path, mode: str = "chat", session_id: str | None = None) -> RunResult:
        if mode not in ("chat", "task", "admin"):
            raise ValueError(f"unknown mode: {mode}")
        cmd = [self.claude_bin, "-p", "--output-format", "json"]
        if mode == "chat":
            cmd += ["--append-system-prompt", CHAT_PROMPT, "--allowedTools", CHAT_TOOLS,
                    "--disallowedTools", SECRET_PATHS]
        elif mode == "admin":
            cmd += ["--append-system-prompt", ADMIN_PROMPT, "--allowedTools", ADMIN_TOOLS,
                    "--disallowedTools", SECRET_PATHS]
        else:
            cmd += ["--append-system-prompt", TASK_PROMPT, "--allowedTools", TASK_TOOLS,
                    "--disallowedTools", TASK_DENY]
        timeout = self.task_timeout if mode == "task" else self.timeout
        if self.model:
            cmd += ["--model", self.model]
        if session_id:
            cmd += ["--resume", session_id]
        # Prompt goes through stdin and the command is a list: message text never touches a shell.
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        try:
            proc = subprocess.run(
                cmd, input=prompt, cwd=cwd, env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return RunResult("error", f"Timed out after {timeout}s.", session_id)
        except FileNotFoundError:
            return RunResult("error", f"Cannot find '{self.claude_bin}'.", session_id)
        return parse_output(proc.stdout, proc.returncode, proc.stderr, session_id)


def parse_output(stdout: str, returncode: int, stderr: str, prev_session: str | None) -> RunResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        detail = (stderr or stdout).strip()[:300] or f"exit code {returncode}"
        return RunResult("error", f"Run failed: {detail}", prev_session)
    usage = data.get("usage") or {}
    failed = bool(data.get("is_error")) or returncode != 0
    return RunResult(
        status="error" if failed else "ok",
        text=(data.get("result") or "").strip(),
        session_id=data.get("session_id") or prev_session,
        input_tokens=usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_usd=data.get("total_cost_usd", 0.0) or 0.0,
        raw=data,
    )


def task_outcome(text: str) -> tuple[str, str]:
    """Read the last SUMMARY:/BLOCKED: line the agent wrote. Returns (state, line)."""
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("SUMMARY:"):
            return "done", line[len("SUMMARY:"):].strip()
        if line.startswith("BLOCKED:"):
            return "blocked", line[len("BLOCKED:"):].strip()
    return "unclear", text.strip().splitlines()[-1] if text.strip() else ""
