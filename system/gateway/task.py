"""Task mode by hand: make worktrees, run the agent, show what changed.

  python -m gateway.task run   --workspace PATH --repos a,b --task 7-foo --prompt "..."
  python -m gateway.task clean --workspace PATH --repos a,b --task 7-foo
"""
import argparse
import json
import sys
from pathlib import Path

from . import config as cfg_mod
from .runner import ClaudeRunner, task_outcome
from .state import atomic_write, write_run_log
from .worktrees import Worktrees


def run_task(cfg: cfg_mod.Config, workspace: Path, repos: list[str], task: str, prompt: str,
             base: str = "main", branch: str | None = None, runner: ClaudeRunner | None = None) -> dict:
    branch = branch or f"task/{task}"
    wts = Worktrees(workspace)
    task_dir = wts.create(task, repos, branch)
    runner = runner or ClaudeRunner(cfg.claude_bin, cfg.model, cfg.run_timeout, cfg.task_timeout)
    record_path = cfg.state_dir / "tasks" / f"{task}.json"
    session = None
    if record_path.exists():  # a second run on the same task continues its session
        session = json.loads(record_path.read_text()).get("session_id")
    result = runner.run(prompt, task_dir, "task", session)
    write_run_log(cfg.state_dir, f"task:{task}", "task", result)
    state, line = task_outcome(result.text) if result.status == "ok" else ("error", result.text)
    report = {
        "task": task, "branch": branch, "status": result.status, "outcome": state, "summary": line,
        "changes": wts.changes(task, repos, base), "tokens": result.input_tokens + result.output_tokens,
        "cost_usd": result.cost_usd, "session_id": result.session_id,
    }
    atomic_write(record_path, json.dumps({**report, "repos": repos, "workspace": str(workspace)}, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gateway.task")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "clean"):
        p = sub.add_parser(name)
        p.add_argument("--workspace", required=True)
        p.add_argument("--repos", required=True)
        p.add_argument("--task", required=True)
    sub.choices["run"].add_argument("--prompt", required=True)
    sub.choices["run"].add_argument("--base", default="main")
    args = ap.parse_args(argv)
    cfg = cfg_mod.load()
    repos = [r for r in args.repos.split(",") if r]
    if args.cmd == "run":
        print(json.dumps(run_task(cfg, Path(args.workspace), repos, args.task, args.prompt, args.base), indent=2))
    else:
        Worktrees(Path(args.workspace)).remove(args.task, repos, f"task/{args.task}")
        print("cleaned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
