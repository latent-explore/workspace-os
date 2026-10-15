"""Dispatcher: picks ready tasks, runs an agent episode, wakes sleeping tasks, delivers the outbox.

It never decides how a task ends. The agent sets the status. The dispatcher only guarantees
that no task is lost: a crashed run is retried, a silent exit is flagged, a sleeper is woken.
"""
import logging
import subprocess
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs, outbox
from .registry import Registry
from .tasks import TaskError, TaskStore
from .worktrees import Worktrees, WorktreeError

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3  # a task whose runs keep crashing is blocked and shown to me
CHECK_TIMEOUT = 60


def first_prompt(task: dict, wos_bin: str) -> str:
    parts = [f"Task #{task['id']} in workspace '{task['workspace']}': {task['title']}"]
    if task["description"]:
        parts.append(task["description"])
    if task["repos"]:
        parts.append("Repos in this folder: " + ", ".join(task["repos"]) + ". Each is a git worktree on your task branch.")
    parts.append(f"Report with `{wos_bin} task ...` as the task skill describes, and leave the task in a state before you exit.")
    return "\n\n".join(parts)


def resume_prompt(task: dict, wos_bin: str) -> str:
    reason = task.get("resume_reason") or ""
    human = [c for c in task["comments"] if c["by"] == "me"][-3:]
    parts = [f"You are resuming task #{task['id']}: {task['title']}."]
    if reason:
        parts.append(f"You asked to be resumed because: {reason}")
    if human:
        parts.append("Latest messages from the user:\n" + "\n".join(f"- {c['text']}" for c in human))
    parts.append(f"Run `{wos_bin} task show` for the full record, continue, and leave the task in a state before you exit.")
    return "\n\n".join(parts)


class Dispatcher:
    def __init__(self, cfg, store: TaskStore, registry: Registry, runner, channel, pool, write_log):
        self.cfg, self.store, self.registry = cfg, store, registry
        self.runner, self.channel, self.pool, self.write_log = runner, channel, pool, write_log
        self.live: set[int] = set()
        self._lock = threading.Lock()
        self.wos_bin = str(cfg.repo_dir / "bin" / "wos")

    # ----- one pass, called every few seconds -----
    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        self.deliver_outbox()
        self.wake_sleepers(now)
        self.pick_up()

    def recover(self) -> None:
        """At startup nothing is running. Anything marked running was cut off by a crash or restart."""
        for task in self.store.list(statuses=("running",)):
            if task["attempts"] >= MAX_ATTEMPTS:
                self.store.comment(task["id"], f"Stopped after {task['attempts']} attempts.", by="system")
                self.store.set_status(task["id"], "blocked")
                self.notify(task, f"Task #{task['id']} blocked: it keeps getting cut off ({task['title']}).")
            else:
                self.store.comment(task["id"], "Run was cut off by a restart; trying again.", by="system")
                self.store.set_status(task["id"], "ready")

    def notify(self, task: dict, text: str) -> None:
        outbox.enqueue(self.cfg.state_dir, text, task.get("notify"))

    def deliver_outbox(self) -> None:
        def send(target, text):
            chat_id = (target or {}).get("chat_id") or self.cfg.allowed_user_id
            if chat_id is None:
                raise RuntimeError("no one to send to")
            self.channel.notify(chat_id, text, (target or {}).get("thread_id"))

        outbox.drain(self.cfg.state_dir, send)

    # ----- sleeping tasks -----
    def wake_sleepers(self, now: datetime) -> None:
        for task in self.store.list(statuses=("waiting",)):
            wake = task.get("wake")
            if not wake or now < datetime.fromisoformat(wake["at"]):
                continue
            if self.wake_condition_met(task, wake):
                reason = wake.get("reason") or "wake-up time reached"
                self.store.comment(task["id"], f"Woken: {reason}", by="system")
                self.store.update(task["id"], lambda t, reason=reason: t.update(status="ready", wake=None, resume_reason=reason))
                continue
            checks = wake["checks"] + 1
            if checks >= wake["max_checks"]:
                self.store.comment(task["id"], f"Gave up waiting after {checks} checks.", by="system")
                self.store.set_status(task["id"], "blocked")
                self.notify(task, f"Task #{task['id']} blocked: gave up waiting ({task['title']}).")
                continue

            def later(t, checks=checks, wake=wake):
                t["wake"]["checks"] = checks
                t["wake"]["at"] = (now + timedelta(seconds=wake["every_s"])).isoformat(timespec="seconds")

            self.store.update(task["id"], later)

    def wake_condition_met(self, task: dict, wake: dict) -> bool:
        """Free checks only: job state and an exit code. No tokens are spent here."""
        if wake.get("when_job_done") and not jobs.all_done(self.cfg.state_dir, task["id"], task["jobs"]):
            return False
        if wake.get("check"):
            try:
                proc = subprocess.run(["bash", "-c", wake["check"]], cwd=self.work_dir(task), capture_output=True,
                                      timeout=CHECK_TIMEOUT)
                return proc.returncode == 0
            except (subprocess.TimeoutExpired, OSError):
                return False
        return True

    # ----- ready tasks -----
    def pick_up(self) -> None:
        for task in self.store.list(statuses=("ready",)):
            with self._lock:
                if task["id"] in self.live or len(self.live) >= self.cfg.max_runs:
                    continue
                try:
                    self.store.claim(task["id"])
                except TaskError:
                    continue
                self.live.add(task["id"])
            self.pool.submit(self.run_episode, task["id"])

    def workspace_path(self, task: dict) -> Path | None:
        entry = self.registry.all().get(task["workspace"])
        return Path(entry["path"]) if entry else None

    def work_dir(self, task: dict) -> Path:
        base = self.workspace_path(task) or self.cfg.chat_dir
        task_dir = base / ".worktrees" / f"t{task['id']}"
        return task_dir if task_dir.exists() else base

    def run_episode(self, task_id: int) -> None:
        try:
            task = self.store.get(task_id)
            base = self.workspace_path(task)
            if base is None:
                self.block(task, f"Unknown workspace {task['workspace']!r}.")
                return
            cwd = base
            if task["repos"]:
                branch = task["branch"] or f"task/{task_id}"
                cwd = Worktrees(base).create(f"t{task_id}", task["repos"], branch)
                self.store.update(task_id, lambda t: t.update(branch=branch))
            prompt = (resume_prompt if task["session_id"] else first_prompt)(task, self.wos_bin)
            env = {"WOS_TASK_ID": str(task_id), "WOS_BIN": self.wos_bin}
            ctx = {"workspace": task["workspace"], "task_id": task_id, "notify": task.get("notify"),
                   "agent": self.registry.all().get(task["workspace"], {}).get("agent")}
            result = self.runner.run(prompt, cwd, "task", task["session_id"], env, ctx)
            self.write_log(self.cfg.state_dir, f"task:{task_id}", "task", result)
            if result.session_id:
                self.store.update(task_id, lambda t: t.update(session_id=result.session_id))
            after = self.store.get(task_id)
            if result.status == "error":
                self.block(after, f"The run failed: {result.text[:300]}")
            elif after["status"] == "running":  # agent left without saying how it ended
                self.store.comment(task_id, f"Agent exited without setting a state. Last words: {result.text[:500]}", by="system")
                self.block(after, "The agent exited without saying how it ended.")
        except (WorktreeError, TaskError) as exc:
            self.block(self.store.get(task_id), str(exc))
        except Exception as exc:  # never lose a task to a bug
            log.exception("episode for task %s crashed", task_id)
            self.block(self.store.get(task_id), f"Internal error: {exc}")
        finally:
            with self._lock:
                self.live.discard(task_id)

    def block(self, task: dict, why: str) -> None:
        self.store.comment(task["id"], why, by="system")
        self.store.set_status(task["id"], "blocked")
        self.notify(task, f"Task #{task['id']} blocked: {why}\n{task['title']}")

    def start(self, interval: int = 10) -> threading.Thread:
        self.recover()

        def loop():
            stop = threading.Event()
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.exception("dispatcher tick failed")
                stop.wait(interval)

        thread = threading.Thread(target=loop, name="dispatcher", daemon=True)
        thread.start()
        return thread
