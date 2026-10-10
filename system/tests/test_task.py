import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from gateway import config as cfg_mod
from gateway.runner import ClaudeRunner, task_outcome
from gateway.task import run_task
from gateway.worktrees import WorktreeError, Worktrees

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def make_repo(path: Path) -> None:
    path.mkdir(parents=True)
    for cmd in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run([*GIT, *cmd], cwd=path, check=True)


FAKE_AGENT = """#!/bin/sh
cat >/dev/null
cd api && echo hi > new.txt && git add -A && git -c user.name=t -c user.email=t@t commit -qm "add new.txt"
echo '{"result":"done it\\nSUMMARY: added new.txt","session_id":"s1","usage":{}}'
"""


class OutcomeTests(unittest.TestCase):
    def test_summary_and_blocked(self):
        self.assertEqual(task_outcome("x\nSUMMARY: did it"), ("done", "did it"))
        self.assertEqual(task_outcome("BLOCKED: need a key"), ("blocked", "need a key"))
        self.assertEqual(task_outcome("just text")[0], "unclear")
        self.assertEqual(task_outcome("")[0], "unclear")


class WorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ws = Path(self.tmp.name) / "ws"
        make_repo(self.ws / "api")
        make_repo(self.ws / "web")

    def test_create_makes_one_worktree_per_repo_on_same_branch(self):
        wts = Worktrees(self.ws)
        task_dir = wts.create("7-foo", ["api", "web"], "task/7-foo")
        for repo in ("api", "web"):
            branch = subprocess.run(["git", "branch", "--show-current"], cwd=task_dir / repo,
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(branch, "task/7-foo")
        wts.create("7-foo", ["api", "web"], "task/7-foo")  # safe to call twice

    def test_bad_names_are_refused(self):
        wts = Worktrees(self.ws)
        for task in ("../evil", "a/b", "", ".hidden"):
            with self.assertRaises(WorktreeError, msg=task):
                wts.create(task, ["api"], "task/x")
        with self.assertRaises(WorktreeError):
            wts.create("ok", ["../api"], "task/x")
        with self.assertRaises(WorktreeError):
            wts.create("ok", ["missing"], "task/x")

    def test_remove_cleans_up(self):
        wts = Worktrees(self.ws)
        wts.create("7-foo", ["api"], "task/7-foo")
        wts.remove("7-foo", ["api"], "task/7-foo")
        self.assertFalse((self.ws / ".worktrees" / "7-foo").exists())

    def test_full_task_run_with_fake_agent(self):
        fake = Path(self.tmp.name) / "claude"
        fake.write_text(FAKE_AGENT)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        state = Path(self.tmp.name) / "state"
        cfg = cfg_mod.Config("", None, state / "chat", state, None, 1, str(fake), 60, 60, state / "s.yaml")
        report = run_task(cfg, self.ws, ["api"], "7-foo", "add a file", runner=ClaudeRunner(str(fake)))
        self.assertEqual((report["status"], report["outcome"], report["summary"]), ("ok", "done", "added new.txt"))
        self.assertEqual(report["changes"]["api"]["files"], ["new.txt"])
        self.assertEqual(len(report["changes"]["api"]["commits"]), 1)
        self.assertEqual(json.loads((state / "tasks" / "7-foo.json").read_text())["session_id"], "s1")


if __name__ == "__main__":
    unittest.main()
