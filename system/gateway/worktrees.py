"""Worktrees: one folder per task, one git worktree per repo, same branch name in each.

workspace/
├── api/ web/                  main checkouts, kept clean
└── .worktrees/<task>/
    ├── api/                   branch <branch>
    └── web/                   branch <branch>
"""
import re
import subprocess
from pathlib import Path

SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class WorktreeError(RuntimeError):
    pass


def _git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed in {cwd}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _check_name(kind: str, name: str) -> str:
    # Names become folder and branch names, so refuse anything that could escape the workspace.
    if not SAFE.fullmatch(name) or ".." in name:
        raise WorktreeError(f"bad {kind} name: {name!r}")
    return name


class Worktrees:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).expanduser().resolve()

    def task_dir(self, task: str) -> Path:
        return self.workspace / ".worktrees" / _check_name("task", task)

    def _repo(self, name: str) -> Path:
        repo = self.workspace / _check_name("repo", name)
        if not (repo / ".git").exists():
            raise WorktreeError(f"{repo} is not a git repo")
        return repo

    def create(self, task: str, repos: list[str], branch: str) -> Path:
        """Make the task folder with one worktree per repo. Safe to call again: existing ones are kept."""
        _check_name("branch", branch.replace("/", "-"))
        task_dir = self.task_dir(task)
        for name in repos:
            repo, target = self._repo(name), task_dir / name
            if target.exists():
                continue
            task_dir.mkdir(parents=True, exist_ok=True)
            exists = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo
            ).returncode == 0
            if exists:
                _git("worktree", "add", str(target), branch, cwd=repo)
            else:
                _git("worktree", "add", "-b", branch, str(target), cwd=repo)  # from the repo's current HEAD
        return task_dir

    def changes(self, task: str, repos: list[str], base: str = "main") -> dict[str, dict]:
        """What the task changed in each repo, compared with its base branch."""
        out = {}
        for name in repos:
            wt = self.task_dir(task) / name
            commits = _git("log", "--format=%h %s", f"{base}..HEAD", cwd=wt).splitlines()
            files = _git("diff", "--name-only", f"{base}...HEAD", cwd=wt).splitlines()
            dirty = bool(_git("status", "--porcelain", cwd=wt))
            out[name] = {"commits": commits, "files": files, "uncommitted": dirty}
        return out

    def remove(self, task: str, repos: list[str], branch: str, delete_branch: bool = True) -> None:
        """Remove worktrees. Refuses to delete a branch that has unmerged commits unless it was pushed."""
        task_dir = self.task_dir(task)
        for name in repos:
            repo, target = self._repo(name), task_dir / name
            if target.exists():
                _git("worktree", "remove", "--force", str(target), cwd=repo)
            if delete_branch:
                subprocess.run(["git", "branch", "-d", branch], cwd=repo, capture_output=True)  # -d: only if merged
            _git("worktree", "prune", cwd=repo)
        if task_dir.exists() and not any(task_dir.iterdir()):
            task_dir.rmdir()
