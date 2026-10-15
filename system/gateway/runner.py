"""Runner: starts Claude Code headless as a named agent and returns one standard result.

Agents are files in plugins (`base/plugin/agents/*.md`, plus any installed plugin and any
workspace's own `.claude/agents`). The runner only picks which one to start.
"""
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import tokens

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_PLUGIN = REPO_ROOT / "base" / "plugin"
WOS_BIN = REPO_ROOT / "bin" / "wos"
TASK_SKILL = BUILTIN_PLUGIN / "skills" / "task" / "SKILL.md"

# Which agent runs in which mode, unless the workspace picked another (ctx["agent"]).
DEFAULT_AGENT = {"chat": "wos:workspace", "task": "wos:workspace", "admin": "wos:admin"}
SECRET_PATHS = "Read(~/.config/workspace-os/**)"        # nobody but the owner's card may read the secrets
SECRET_ENV = ("TELEGRAM_BOT_TOKEN",)

# With approvals on, only reads are pre-allowed and every other call goes to the permission check.
READS = "Read,Glob,Grep"
# For admin the same paths do not get denied: Claude Code is told to ask, and the ask goes to the permission check.
ASK_SECRETS = json.dumps({"permissions": {"ask": [f"{tool}(~/.config/workspace-os/**)" for tool in ("Read", "Glob", "Grep")]}})
# Hard denies for ordinary agents. Admin gets none: for admin the same things become a card.
HARD_DENY = f"Bash(sudo:*),Bash(gh:*),{SECRET_PATHS}"
# Without approvals (manual task CLI, WOS_APPROVALS=0) fixed lists apply instead.
LEGACY_ALLOW = {"chat": READS, "admin": READS + ",Bash(bin/wos:*),Bash(./bin/wos:*)", "task": READS + ",Edit,Write,Bash"}
LEGACY_DENY = {"chat": SECRET_PATHS, "admin": SECRET_PATHS,
               "task": f"Bash(git push:*),Bash(gh:*),Bash(sudo:*),WebFetch,WebSearch,{SECRET_PATHS}"}


def task_prompt() -> str | None:
    """The task skill text, appended to the agent for task runs so it always knows the workflow."""
    try:
        text = TASK_SKILL.read_text()
    except FileNotFoundError:
        return None
    return (text.split("---", 2)[-1] if text.startswith("---") else text).strip()


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
                 task_timeout: int = 1800, state_dir: Path | None = None, approvals: bool = False,
                 workspace_mcp=None, approval_wait: int = 300, strict_mcp: bool = False, plugins=None):
        self.claude_bin = claude_bin
        self.model = model
        self.timeout = timeout
        self.task_timeout = task_timeout
        self.state_dir, self.approvals = state_dir, approvals and state_dir is not None
        self.workspace_mcp, self.approval_wait, self.strict_mcp = workspace_mcp, approval_wait, strict_mcp
        self.plugins = plugins           # object with .dirs(); None means just the built-in plugin

    def plugin_dirs(self) -> list[Path]:
        return self.plugins.dirs() if self.plugins else [BUILTIN_PLUGIN]

    def run(self, prompt: str, cwd: Path, mode: str = "chat", session_id: str | None = None,
            extra_env: dict | None = None, ctx: dict | None = None) -> RunResult:
        if mode not in DEFAULT_AGENT:
            raise ValueError(f"unknown mode: {mode}")
        agent = (ctx or {}).get("agent") or DEFAULT_AGENT[mode]
        cmd = [self.claude_bin, "-p", "--output-format", "json", "--agent", agent]
        for plugin in self.plugin_dirs():
            cmd += ["--plugin-dir", str(plugin)]
        if mode == "task" and task_prompt():
            cmd += ["--append-system-prompt", task_prompt()]
        env_extra = {"WOS_BIN": str(WOS_BIN), **(extra_env or {})}
        mcp_file = admin_token = None
        if ctx is not None and self.approvals:
            from .mcp import write_run_config
            servers = self.workspace_mcp.servers(ctx.get("workspace")) if self.workspace_mcp else {}
            mcp_file = write_run_config(self.state_dir, {**ctx, "mode": mode}, servers, self.approval_wait)
            cmd += ["--allowedTools", READS, "--mcp-config", str(mcp_file), "--permission-prompt-tool", "mcp__wos__approve"]
            if mode != "admin":
                cmd += ["--disallowedTools", HARD_DENY]
            else:
                cmd += ["--settings", ASK_SECRETS]
            if self.strict_mcp:       # off by default: connectors (Gmail...) stay visible, every call is still checked
                cmd.append("--strict-mcp-config")
            env_extra.update({"WOS_AGENT": "1", "WOS_WORKSPACE": ctx.get("workspace") or ""})
            if mode == "admin":
                admin_token = tokens.issue(self.state_dir)     # lets bin/wos accept changes the owner approved on a card
                env_extra["WOS_ADMIN"] = admin_token
        else:
            cmd += ["--allowedTools", LEGACY_ALLOW[mode], "--disallowedTools", LEGACY_DENY[mode]]
        if self.model:
            cmd += ["--model", self.model]
        if session_id:
            cmd += ["--resume", session_id]
        timeout = self.task_timeout if mode == "task" else self.timeout
        # The prompt goes through stdin and the command is a list: message text never touches a shell.
        env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
        env.update(env_extra)
        try:
            return self._run(cmd, prompt, cwd, env, timeout, session_id)
        finally:
            if mcp_file:
                mcp_file.unlink(missing_ok=True)
            if admin_token:
                tokens.revoke(self.state_dir, admin_token)

    def _run(self, cmd, prompt, cwd, env, timeout, session_id) -> RunResult:
        try:
            proc = subprocess.run(cmd, input=prompt, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
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
