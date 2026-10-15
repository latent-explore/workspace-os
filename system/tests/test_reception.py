import subprocess
import tempfile
import unittest
from pathlib import Path

from gateway import config as cfg_mod
from gateway.main import Gateway, normalize
from gateway.registry import Chats, Registry, RegistryError
from gateway.telegram import Message
from gateway.workspace import create_workspace, parse_repo

ME = 42


class FakeChannel:
    def __init__(self):
        self.sent = []

    def is_allowed(self, user_id):
        return user_id == ME

    def notify(self, chat_id, text, thread_id=None):
        self.sent.append(text)

    def typing(self, *a):
        pass


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, prompt, cwd, mode, session_id=None, extra_env=None, ctx=None):
        self.calls.append((prompt, cwd, mode))
        from gateway.runner import RunResult
        return RunResult("ok", "reply", "sid")


def msg(text, chat_id=-100, kind="supergroup", user=ME, title="G"):
    return Message(f"{chat_id}:0", chat_id, None, user, text, 1, kind, title)


class ReceptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = cfg_mod.Config("t", ME, self.root / "chat", self.root / "state", None, 1, "claude", 60, 60,
                                  self.root / "s.yaml", self.root / "repo", self.root / "ws", self.root / "reg.yaml",
                                  admin_dir=self.root / "admin")
        self.cfg.state_dir.mkdir()
        self.channel, self.runner = FakeChannel(), FakeRunner()
        self.gw = Gateway(self.cfg, self.channel, self.runner)

    def test_normalize_strips_bot_name(self):
        self.assertEqual(normalize("/new@MyBot  hello "), "/new hello")
        self.assertEqual(normalize("  plain text "), "plain text")

    def test_routes(self):
        self.assertEqual(self.gw.route(msg("x", 7, "private")), (self.cfg.admin_dir, "admin", None))   # only I can reach it
        self.assertIsNone(self.gw.route(msg("x", -1)))                     # unknown group: nowhere
        self.gw.handle(msg("/admin", -1))
        self.assertEqual(self.gw.route(msg("x", -1)), (self.cfg.admin_dir, "admin", None))
        self.gw.registry.add("inv", self.root / "ws" / "inv")
        self.gw.registry.bind("inv", -2)
        self.assertEqual(self.gw.route(msg("x", -2)), (self.root / "ws" / "inv", "chat", "inv"))

    def test_admin_can_only_be_set_once_and_not_in_private(self):
        self.gw.handle(msg("/admin", 7, "private"))
        self.assertIn("inside the group", self.channel.sent[-1])
        self.gw.handle(msg("/admin", -1))
        self.gw.handle(msg("/admin", -2))
        self.assertIn("already another group", self.channel.sent[-1])
        self.assertEqual(self.gw.chats.admin(), -1)

    def test_admin_commands_only_in_reception(self):
        self.gw.handle(msg("/admin", -1))
        self.gw.handle(msg("/groups", -2))
        self.assertEqual(self.channel.sent[-1], "Use this in Reception.")
        self.gw.registry.add("inv", self.root / "x")
        self.gw.handle(msg("/groups", -1))
        self.assertIn("-2 | G | not linked", self.channel.sent[-1])
        self.gw.handle(msg("/bind -2 inv", -1))
        self.assertIn("Linked", self.channel.sent[-1])
        self.gw.handle(msg("/groups", -1))
        self.assertIn("-> inv", self.channel.sent[-1])
        self.gw.handle(msg("/bind -3 nope", -1))
        self.assertIn("Error", self.channel.sent[-1])

    def test_unbound_group_runs_nothing(self):
        self.gw.run_chat(msg("hello", -9))
        self.assertEqual(self.runner.calls, [])
        self.assertIn("not linked", self.channel.sent[-1])

    def test_reception_runs_in_admin_mode_in_repo_dir(self):
        self.gw.handle(msg("/admin", -1))
        self.gw.run_chat(msg("create a workspace", -1))
        self.assertEqual(self.runner.calls[-1], ("create a workspace", self.cfg.admin_dir, "admin"))

    def test_strangers_are_ignored(self):
        self.gw.handle(msg("/admin", -1, user=999))
        self.assertIsNone(self.gw.chats.admin())
        self.assertEqual(self.channel.sent, [])


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.reg = Registry(self.root / "reg.yaml")
        self.src = self.root / "src" / "api"           # a local stand-in for a GitHub repo
        self.src.mkdir(parents=True)
        for cmd in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run(["git", *cmd], cwd=self.src, check=True)
        self.template = Path(cfg_mod.REPO_ROOT) / "base" / "template"

    def local_clone(self, url, dest):
        subprocess.run(["git", "clone", "-q", url, str(dest)], check=True)

    def make(self, name="inv", urls=None):
        return create_workspace(name, urls if urls is not None else [str(self.src)], self.root / "ws", self.reg,
                                self.template, parse=lambda u: (Path(u).name, u), clone=self.local_clone)

    def test_parse_repo_accepts_only_github_https(self):
        self.assertEqual(parse_repo("https://github.com/o/r.git"), ("r", "https://github.com/o/r.git"))
        for bad in ("git@github.com:o/r.git", "http://github.com/o/r", "https://evil.com/o/r", "file:///etc", "https://github.com/o/../r"):
            with self.assertRaises(RegistryError, msg=bad):
                parse_repo(bad)

    def test_create_workspace(self):
        out = self.make()
        ws = Path(out["path"])
        self.assertTrue((ws / "api" / ".git").exists())
        self.assertIn("# Workspace: inv", (ws / "CLAUDE.md").read_text())
        self.assertIn("api/", (ws / ".gitignore").read_text())
        self.assertIn("api", (ws / "repos.yaml").read_text())
        self.assertTrue(out["committed"])
        self.assertEqual(self.reg.all()["inv"]["path"], str(ws))

    def test_bad_names_and_duplicates(self):
        for bad in ("Inv", "../x", "a", "1abc", "x y"):
            with self.assertRaises(RegistryError, msg=bad):
                self.make(bad)
        self.make("inv")
        with self.assertRaises(RegistryError):
            self.make("inv")

    def test_failed_clone_leaves_nothing_behind(self):
        with self.assertRaises(Exception):
            self.make("broken", [str(self.root / "missing")])
        self.assertFalse((self.root / "ws" / "broken").exists())
        self.assertNotIn("broken", self.reg.all())

    def test_chat_cannot_be_bound_twice(self):
        self.make("one")
        self.make("two")
        self.reg.bind("one", -5)
        with self.assertRaises(RegistryError):
            self.reg.bind("two", -5)


if __name__ == "__main__":
    unittest.main()
