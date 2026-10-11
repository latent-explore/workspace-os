import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from gateway import cli, jobs, outbox
from gateway import config as cfg_mod
from gateway.dispatcher import Dispatcher
from gateway.registry import Registry
from gateway.runner import RunResult
from gateway.tasks import TaskError, TaskStore, parse_duration

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


class SyncPool:
    def submit(self, fn, *args):
        fn(*args)


class Sent:
    def __init__(self):
        self.messages = []

    def notify(self, chat_id, text, thread_id=None):
        self.messages.append((chat_id, thread_id, text))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.cfg = cfg_mod.Config("t", 42, self.root / "chat", self.state, None, 1, "claude", 60, 60,
                                  self.root / "s.yaml", self.root / "repo", self.root / "ws", self.state / "reg.yaml")
        self.store = TaskStore(self.state)
        self.registry = Registry(self.cfg.registry_path)
        self.ws = self.root / "ws" / "inv"
        (self.ws / "api").mkdir(parents=True)
        for cmd in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run([*GIT, *cmd], cwd=self.ws / "api", check=True)
        self.registry.add("inv", self.ws)
        self.channel = Sent()


class StoreTests(Base):
    def test_ids_and_lifecycle(self):
        a = self.store.create("inv", "first")
        b = self.store.create("inv", "second")
        self.assertEqual((a["id"], b["id"]), (1, 2))
        self.assertEqual(self.store.next_ready()["id"], 1)
        self.store.claim(1)
        self.assertEqual(self.store.next_ready()["id"], 2)
        with self.assertRaises(TaskError):
            self.store.claim(1)  # already running
        self.store.comment(1, "hello")
        self.store.link(1, "https://x/pr/1", "PR")
        self.store.set_status(1, "review")
        t = self.store.get(1)
        self.assertEqual((t["status"], t["comments"][0]["text"], t["links"][0]["label"], t["attempts"]), ("review", "hello", "PR", 1))

    def test_bad_input(self):
        with self.assertRaises(TaskError):
            self.store.create("inv", "  ")
        self.store.create("inv", "x")
        with self.assertRaises(TaskError):
            self.store.set_status(1, "finished-ish")
        with self.assertRaises(TaskError):
            self.store.get(99)
        for bad in ("", "0m", "5x", "m5"):
            with self.assertRaises(TaskError, msg=bad):
                parse_duration(bad)
        self.assertEqual(parse_duration("6h"), timedelta(hours=6))

    def test_status_change_clears_wake(self):
        self.store.create("inv", "x")
        self.store.set_wake(1, timedelta(hours=1))
        self.assertEqual(self.store.get(1)["status"], "waiting")
        self.store.set_status(1, "ready")
        self.assertIsNone(self.store.get(1)["wake"])


class DispatcherTests(Base):
    def make(self, agent):
        self.calls = []

        class Runner:
            def run(inner, prompt, cwd, mode, session_id=None, extra_env=None, ctx=None):
                self.calls.append((prompt, cwd, mode, session_id, extra_env, ctx))
                return agent(prompt, cwd, extra_env)

        return Dispatcher(self.cfg, self.store, self.registry, Runner(), self.channel, SyncPool(), lambda *a: None)

    def test_agent_sets_review_and_session_is_saved(self):
        self.store.create("inv", "do it", repos=["api"])
        d = self.make(lambda p, c, e: (self.store.set_status(int(e["WOS_TASK_ID"]), "review"), RunResult("ok", "done", "s1"))[1])
        d.tick()
        t = self.store.get(1)
        self.assertEqual((t["status"], t["session_id"], t["branch"]), ("review", "s1", "task/1"))
        prompt, cwd, mode, session, env, ctx = self.calls[0]
        self.assertEqual((mode, session, env["WOS_TASK_ID"]), ("task", None, "1"))
        self.assertEqual((ctx["workspace"], ctx["task_id"]), ("inv", 1))
        self.assertTrue((cwd / "api" / ".git").exists())          # a worktree per repo
        self.assertIn("do it", prompt)

    def test_silent_exit_is_blocked_and_reported(self):
        self.store.create("inv", "x")
        d = self.make(lambda p, c, e: RunResult("ok", "I think I am done", "s1"))
        d.tick()
        d.tick()
        self.assertEqual(self.store.get(1)["status"], "blocked")
        d.deliver_outbox()
        self.assertTrue(any("blocked" in m[2] for m in self.channel.messages))
        self.assertEqual(self.channel.messages[0][0], 42)          # no target: my private chat

    def test_failed_run_blocks(self):
        self.store.create("inv", "x")
        self.make(lambda p, c, e: RunResult("error", "boom")).tick()
        t = self.store.get(1)
        self.assertEqual(t["status"], "blocked")
        self.assertIn("boom", t["comments"][-1]["text"])

    def test_unknown_workspace_blocks(self):
        self.store.create("ghost", "x")
        self.make(lambda p, c, e: RunResult("ok", "")).tick()
        self.assertEqual(self.store.get(1)["status"], "blocked")

    def test_sleep_wake_resume_keeps_the_session(self):
        self.store.create("inv", "train")
        slept = []

        def agent(prompt, cwd, env):
            tid = int(env["WOS_TASK_ID"])
            if not slept:
                slept.append(1)
                self.store.set_wake(tid, timedelta(seconds=1), reason="training")
            else:
                self.store.set_status(tid, "review")
            return RunResult("ok", "", "S")

        d = self.make(agent)
        d.tick()
        self.assertEqual(self.store.get(1)["status"], "waiting")
        d.tick(datetime.now())                                     # too early: still asleep
        self.assertEqual(self.store.get(1)["status"], "waiting")
        d.wake_sleepers(datetime.now() + timedelta(seconds=5))     # time has come
        self.assertEqual(self.store.get(1)["status"], "ready")
        d.pick_up()
        self.assertEqual(self.store.get(1)["status"], "review")
        self.assertEqual(self.calls[1][3], "S")                    # same session
        self.assertIn("resuming", self.calls[1][0])
        self.assertIn("training", self.calls[1][0])

    def test_check_script_decides_the_wake(self):
        self.store.create("inv", "x")
        flag = self.root / "ready.flag"
        self.store.set_wake(1, timedelta(seconds=1), every=timedelta(seconds=1), check=f"test -e {flag}")
        d = self.make(lambda p, c, e: RunResult("ok", ""))
        later = datetime.now() + timedelta(seconds=5)
        d.wake_sleepers(later)
        t = self.store.get(1)
        self.assertEqual((t["status"], t["wake"]["checks"]), ("waiting", 1))   # check failed: asleep, rescheduled
        flag.write_text("x")
        d.wake_sleepers(later + timedelta(seconds=5))
        self.assertEqual(self.store.get(1)["status"], "ready")

    def test_gives_up_after_max_checks(self):
        self.store.create("inv", "x")
        self.store.set_wake(1, timedelta(seconds=1), check="false", max_checks=2)
        d = self.make(lambda p, c, e: RunResult("ok", ""))
        t0 = datetime.now() + timedelta(seconds=5)
        d.wake_sleepers(t0)
        d.wake_sleepers(t0 + timedelta(hours=2))
        self.assertEqual(self.store.get(1)["status"], "blocked")

    def test_recover_after_restart(self):
        for title in ("a", "b"):
            self.store.create("inv", title)
        self.store.claim(1)
        self.store.claim(2)
        self.store.update(2, lambda t: t.update(attempts=3))
        d = self.make(lambda p, c, e: RunResult("ok", ""))
        d.recover()
        self.assertEqual(self.store.get(1)["status"], "ready")
        self.assertEqual(self.store.get(2)["status"], "blocked")

    def test_run_cap_is_respected(self):
        for title in ("a", "b"):
            self.store.create("inv", title)

        class Hold:
            def __init__(self):
                self.queued = []

            def submit(self, fn, *args):
                self.queued.append((fn, args))

        d = self.make(lambda p, c, e: RunResult("ok", ""))
        d.pool = Hold()
        d.pick_up()
        d.pick_up()
        self.assertEqual(len(d.pool.queued), 1)                    # max_runs is 1
        self.assertEqual(self.store.get(2)["status"], "ready")


class OutboxAndCliTests(Base):
    def test_outbox_keeps_order_and_stops_on_failure(self):
        outbox.enqueue(self.state, "one")
        outbox.enqueue(self.state, "two", {"chat_id": 5, "thread_id": 6})
        got = []

        def flaky(target, text):
            if text == "two" and not got.count("failed"):
                got.append("failed")
                raise RuntimeError("network")
            got.append(text)

        self.assertEqual(outbox.drain(self.state, flaky), 1)
        self.assertEqual(outbox.drain(self.state, flaky), 1)
        self.assertEqual(got, ["one", "failed", "two"])

    def run_cli(self, *argv, env=None):
        environ = {"WOS_STATE_DIR": str(self.state), "WOS_REGISTRY": str(self.cfg.registry_path), **(env or {})}
        out = StringIO()
        with mock.patch.dict(os.environ, environ), redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_agent_flow_through_cli(self):
        self.run_cli("task", "create", "--workspace", "inv", "--title", "Train it")
        env = {"WOS_TASK_ID": "1"}
        self.run_cli("task", "comment", "starting", env=env)
        self.run_cli("task", "link", "https://x/pr/1", "--label", "PR", env=env)
        code, out = self.run_cli("task", "resume", "--in", "6h", "--when-job-done", "--reason", "training", env=env)
        self.assertEqual(code, 0)
        t = self.store.get(1)
        self.assertEqual((t["status"], t["wake"]["when_job_done"], t["wake"]["reason"]), ("waiting", True, "training"))
        self.run_cli("task", "status", "review", env=env)
        self.assertEqual(len(list((self.state / "outbox").glob("*.json"))), 1)   # review tells me
        code, out = self.run_cli("task", "show", env=env)
        self.assertIn("starting", out)

    def test_needs_a_task_id(self):
        with mock.patch.dict(os.environ, {"WOS_STATE_DIR": str(self.state)}), redirect_stdout(StringIO()):
            os.environ.pop("WOS_TASK_ID", None)
            self.assertEqual(cli.main(["task", "comment", "x"]), 1)


@unittest.skipUnless(subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True).returncode in (0, 1),
                     "needs a systemd user manager")
class JobTests(Base):
    def test_job_outlives_caller_and_records_exit_code(self):
        unit = jobs.start(self.state, 1, ["bash", "-c", "echo hello; sleep 1; exit 3"], self.root)
        deadline = time.time() + 20
        info = jobs.status(self.state, 1, unit)
        while info["state"] == "running" and time.time() < deadline:
            time.sleep(0.5)
            info = jobs.status(self.state, 1, unit)
        self.assertEqual((info["state"], info["exit_code"]), ("finished", 3))
        self.assertIn("hello", Path(info["log"]).read_text())
        self.assertTrue(jobs.all_done(self.state, 1, [unit]))
        self.assertFalse(jobs.all_done(self.state, 1, []))


if __name__ == "__main__":
    unittest.main()


class ChatCommandTests(Base):
    def setUp(self):
        super().setUp()
        from gateway.main import Gateway
        from gateway.telegram import Message
        self.Message = Message
        (self.ws / "repos.yaml").write_text("repos:\n  api: https://github.com/o/api\n")
        self.registry.bind("inv", -77)

        class Chan(Sent):
            def is_allowed(self, user_id):
                return user_id == 42

            def typing(self, *a):
                pass

        self.chan = Chan()
        self.gw = Gateway(self.cfg, self.chan, mock.Mock())

    def say(self, text, chat=-77):
        self.gw.handle(self.Message(f"{chat}:0", chat, None, 42, text, 1, "supergroup", "G"))
        return self.chan.messages[-1][2]

    def test_task_commands_in_a_linked_group(self):
        self.assertIn("Added #1 [ready] fix login", self.say("/addtask fix login | users get logged out"))
        t = self.store.get(1)
        self.assertEqual((t["workspace"], t["repos"], t["notify"]["chat_id"]), ("inv", ["api"], -77))
        self.assertIn("#1 [ready]", self.say("/tasks"))
        self.assertIn("users get logged out", self.say("/task 1"))
        self.store.set_status(1, "blocked")
        self.assertIn("pick it up", self.say("/reply 1 use the old API"))
        self.assertEqual(self.store.get(1)["status"], "ready")
        self.assertEqual(self.store.get(1)["comments"][-1]["by"], "me")
        self.assertIn("Cancelled", self.say("/cancel 1"))
        self.assertIn("ready again", self.say("/retry 1"))

    def test_bad_usage_gets_a_hint(self):
        self.assertIn("Usage", self.say("/task"))
        self.assertIn("Usage", self.say("/reply 1"))
        self.assertIn("no task 9", self.say("/task 9"))
        self.assertIn("Use /addtask in a linked group", self.say("/addtask nothing", chat=-1))
