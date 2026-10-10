"""Task store: one small JSON file per task. The agent decides how an episode ends; this only remembers."""
import fcntl
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from .state import atomic_write

STATUSES = ("ready", "running", "waiting", "review", "blocked", "done", "cancelled")
OPEN = ("ready", "running", "waiting", "review", "blocked")
DURATION = re.compile(r"(\d+)([smhd])")


class TaskError(ValueError):
    pass


def parse_duration(text: str) -> timedelta:
    m = DURATION.fullmatch(text.strip())
    if not m or int(m.group(1)) == 0:
        raise TaskError(f"bad duration {text!r}: use 30m, 6h, 2d")
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(2)]: int(m.group(1))})


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, state_dir: Path):
        self.dir = state_dir / "tasks"

    @contextmanager
    def _locked(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _path(self, task_id: int) -> Path:
        return self.dir / f"{int(task_id)}.json"

    def _read(self, task_id: int) -> dict:
        try:
            return json.loads(self._path(task_id).read_text())
        except FileNotFoundError:
            raise TaskError(f"no task {task_id}") from None

    def _next_id(self) -> int:
        ids = [int(p.stem) for p in self.dir.glob("*.json") if p.stem.isdigit()]
        return max(ids, default=0) + 1

    def create(self, workspace: str, title: str, description: str = "", repos: list[str] | None = None,
               notify: dict | None = None, source: str = "me") -> dict:
        if not title.strip():
            raise TaskError("a task needs a title")
        with self._locked():
            task = {
                "id": self._next_id(), "workspace": workspace, "title": title.strip(),
                "description": description.strip(), "status": "ready", "repos": repos or [], "branch": None,
                "session_id": None, "notify": notify, "comments": [], "links": [], "wake": None, "jobs": [],
                "attempts": 0, "source": source, "created": now_iso(), "updated": now_iso(),
            }
            atomic_write(self._path(task["id"]), json.dumps(task, indent=2))
            return task

    def get(self, task_id: int) -> dict:
        return self._read(task_id)

    def update(self, task_id: int, change) -> dict:
        """Read, let `change(task)` edit it, write back. One writer at a time."""
        with self._locked():
            task = self._read(task_id)
            change(task)
            task["updated"] = now_iso()
            atomic_write(self._path(task_id), json.dumps(task, indent=2))
            return task

    def list(self, workspace: str | None = None, statuses: tuple[str, ...] | None = None) -> list[dict]:
        tasks = []
        for path in sorted(self.dir.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0):
            try:
                task = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if (workspace is None or task["workspace"] == workspace) and (statuses is None or task["status"] in statuses):
                tasks.append(task)
        return tasks

    # --- the small set of operations an agent needs ---
    def next_ready(self, workspace: str | None = None) -> dict | None:
        ready = self.list(workspace, ("ready",))
        return ready[0] if ready else None

    def claim(self, task_id: int) -> dict:
        def change(t):
            if t["status"] != "ready":
                raise TaskError(f"task {task_id} is {t['status']}, not ready")
            t["status"] = "running"
            t["attempts"] += 1
        return self.update(task_id, change)

    def comment(self, task_id: int, text: str, by: str = "agent") -> dict:
        return self.update(task_id, lambda t: t["comments"].append({"at": now_iso(), "by": by, "text": text.strip()}))

    def set_status(self, task_id: int, status: str) -> dict:
        if status not in STATUSES:
            raise TaskError(f"status must be one of: {', '.join(STATUSES)}")

        def change(t):
            t["status"] = status
            if status != "waiting":
                t["wake"] = None

        return self.update(task_id, change)

    def link(self, task_id: int, url: str, label: str = "") -> dict:
        return self.update(task_id, lambda t: t["links"].append({"url": url, "label": label}))

    def set_wake(self, task_id: int, wait: timedelta, every: timedelta | None = None, check: str | None = None,
                 when_job_done: bool = False, max_checks: int = 50, reason: str = "") -> dict:
        """Put the task to sleep. The dispatcher wakes it when the time and the free check agree."""
        at = datetime.now() + wait

        def change(t):
            t["status"] = "waiting"
            t["wake"] = {"at": at.isoformat(timespec="seconds"), "every_s": int((every or wait).total_seconds()),
                         "check": check, "when_job_done": when_job_done, "checks": 0,
                         "max_checks": max_checks, "reason": reason}

        return self.update(task_id, change)

    def add_job(self, task_id: int, unit: str) -> dict:
        return self.update(task_id, lambda t: t["jobs"].append(unit))


def summarize(task: dict) -> str:
    return f"#{task['id']} [{task['status']}] {task['title']} ({task['workspace']})"


def detail(task: dict, last_comments: int = 5) -> str:
    lines = [summarize(task)]
    if task["description"]:
        lines.append(task["description"][:500])
    if task["wake"]:
        lines.append(f"wakes after {task['wake']['at']}")
    lines += [f"link: {l['url']}" for l in task["links"]]
    lines += [f"- {c['by']}: {c['text'][:300]}" for c in task["comments"][-last_comments:]]
    return "\n".join(lines)
