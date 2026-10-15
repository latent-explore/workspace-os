"""Tests for the findings of the review of agents A-D."""
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from gateway import cli, tokens
from gateway import config as cfg_mod
from gateway.main import Gateway
from gateway.permissions import Permissions, rule_matches
from gateway.plugins import Plugins
from gateway.registry import Registry, RegistryError
from gateway.runner import ClaudeRunner
from gateway.telegram import Message
from tests.test_agents import Base, make_plugin


class AdminTokenTests(Base):
    def test_token_lifecycle(self):
        token = tokens.issue(self.state)
        self.assertTrue(tokens.valid(self.state, token))
        tokens.revoke(self.state, token)
        self.assertFalse(tokens.valid(self.state, token))
        for bad in (None, "", "1", "../../etc/passwd", "x" * 32):
            self.assertFalse(tokens.valid(self.state, bad), bad)

    def test_old_tokens_expire(self):
        token = tokens.issue(self.state)
        (self.state / "run-tokens" / token).write_text("1.0")        # issued in 1970
        self.assertFalse(tokens.valid(self.state, token))

    def run_cli(self, *argv, env):
        environ = {"WOS_STATE_DIR": str(self.state), "WOS_REGISTRY": str(self.registry.path)}
        out = StringIO()
        with mock.patch.dict(os.environ, {**environ, **env}), redirect_stdout(out), redirect_stderr(out):
            for key in ("WOS_AGENT", "WOS_ADMIN"):
                if key not in env:
                    os.environ.pop(key, None)
            return cli.main(list(argv)), out.getvalue()

    def test_a_forged_admin_flag_does_nothing(self):
        for forged in ("1", "true", "deadbeef" * 4):
            code, out = self.run_cli("perms", "grant", "inv", "web", env={"WOS_AGENT": "1", "WOS_ADMIN": forged})
            self.assertEqual(code, 1, forged)
            self.assertNotIn("web", self.perms.caps("inv"))
        token = tokens.issue(self.state)
        tokens.revoke(self.state, token)
        self.assertEqual(self.run_cli("perms", "grant", "inv", "web", env={"WOS_AGENT": "1", "WOS_ADMIN": token})[0], 1)

    def test_the_cli_itself_refuses_workspace_add_and_bind_for_ordinary_agents(self):
        for argv in (("workspace", "add", "newone"), ("bind", "-5", "inv"), ("unbind", "inv")):
            code, out = self.run_cli(*argv, env={"WOS_AGENT": "1"})
            self.assertEqual(code, 1, argv)
            self.assertIn("only the admin agent", out)
        self.assertNotIn("telegram_chat", self.registry.all()["inv"])
        token = tokens.issue(self.state)
        code, _ = self.run_cli("bind", "-5", "inv", env={"WOS_AGENT": "1", "WOS_ADMIN": token})
        self.assertEqual(code, 0)                                   # the admin agent, after its card, may
        code, _ = self.run_cli("unbind", "inv", env={})             # and so may the owner at a terminal
        self.assertEqual(code, 0)

    def test_admin_launcher_refuses_inside_an_agent_run(self):
        code, out = self.run_cli("admin", env={"WOS_AGENT": "1"})
        self.assertEqual(code, 1)
        self.assertIn("owner", out)


class CheckHardeningTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = {"workspace": None, "mode": "admin"}
        self.other = {"workspace": "inv", "mode": "task"}
        self.perms.seed("inv")

    def kind(self, ctx, tool, **inp):
        return self.perms.check(ctx, tool, inp).kind

    def test_nobody_starts_an_admin_from_inside_a_run(self):
        for command in ("bin/wos admin --dangerously-skip-permissions -p hi", "$WOS_BIN admin"):
            self.assertEqual(self.kind(self.other, "Bash", command=command), "deny", command)
            self.assertEqual(self.kind(self.admin, "Bash", command=command), "deny", command)

    def test_forging_the_admin_flag_is_caught_in_the_command_text(self):
        for command in ("env WOS_ADMIN=1 bin/wos perms grant inv web", "WOS_ADMIN=1 bash -c 'x'", "env -u WOS_AGENT bin/wos limits set max_runs 4",
                        "cat /home/wos/.local/state/workspace-os/run-tokens/abc", "echo hi >> permissions.yaml"):
            self.assertEqual(self.kind(self.other, "Bash", command=command), "deny", command)
            self.assertEqual(self.kind(self.admin, "Bash", command=command), "ask", command)

    def test_admin_reading_the_secrets_folder_asks_and_others_are_denied(self):
        env = "/home/wos/.config/workspace-os/env"
        for tool in ("Read", "Grep", "Glob"):
            self.assertEqual(self.kind(self.admin, tool, file_path=env, path=env, pattern=env), "ask", tool)
            self.assertEqual(self.kind(self.other, tool, file_path=env, path=env, pattern=env), "deny", tool)

    def test_system_state_files_are_never_silently_edited_and_never_always(self):
        target = str(self.state / "permissions.yaml")
        self.perms.add_rule("_admin", "Write")                     # even a blanket Write rule cannot reach them
        self.assertEqual(self.kind(self.admin, "Write", file_path=target), "ask")
        self.assertEqual(self.kind(self.other, "Write", file_path=target), "deny")
        self.assertEqual(self.kind(self.admin, "Write", file_path=str(self.state / "tasks" / "1.json")), "ask")
        self.assertIsNone(self.perms.rule_for(self.admin, "Write", {"file_path": target}))
        self.assertIsNone(self.perms.rule_for(self.admin, "Write", {"file_path": "/home/wos/.config/workspace-os/env"}))

    def test_saved_bash_rules_do_not_cover_command_substitution_or_redirects(self):
        self.perms.add_rule("inv", "Bash(ls:*)")
        self.perms.revoke("inv", "shell")
        self.assertEqual(self.kind(self.other, "Bash", command="ls -la"), "allow")
        for command in ("ls $(python3 -c 'print(1)')", "ls `id`", "ls > /home/x/.bashrc", "ls < /etc/passwd", "ls ${HOME}"):
            self.assertEqual(self.kind(self.other, "Bash", command=command), "ask", command)
            self.assertFalse(rule_matches("Bash(ls:*)", "Bash", {"command": command}), command)
            self.assertIsNone(self.perms.rule_for(self.other, "Bash", {"command": command}), command)

    def test_always_allow_is_refused_for_open_ended_commands(self):
        for command in ("find . -delete", "sed -i s/a/b/ f", "tar xf x.tar", "awk '{print}' f", "git -c alias.x=!id x", "npm", "git"):
            self.assertIsNone(self.perms.rule_for(self.other, "Bash", {"command": command}), command)
        self.assertEqual(self.perms.rule_for(self.other, "Bash", {"command": "git status"}), {"kind": "rule", "value": "Bash(git status:*)"})

    def test_ordinary_agents_cannot_create_workspaces_or_link_groups_but_admin_can(self):
        scheduled = {"workspace": None, "mode": "chat"}
        for command in ("$WOS_BIN workspace add x --repo https://github.com/o/r", "bin/wos bind -5 inv", "$WOS_BIN unbind inv"):
            for ctx in (self.other, scheduled):
                self.assertEqual(self.kind(ctx, "Bash", command=command), "deny", command)
            self.assertEqual(self.kind(self.admin, "Bash", command=command), "allow", command)
        self.assertEqual(self.kind(self.other, "Bash", command="$WOS_BIN workspace list"), "allow")
        self.assertEqual(self.kind(self.other, "Bash", command="$WOS_BIN task comment hi"), "allow")


class RunnerAdminSettingsTests(Base):
    def test_admin_runs_carry_an_ask_rule_for_the_secrets_folder(self):
        out = self.root / "argv.json"
        fake = self.root / "claude"
        fake.write_text(f"#!/usr/bin/env python3\nimport json,sys\njson.dump(sys.argv[1:], open('{out}', 'w'))\nsys.stdin.read()\n"
                        "print(json.dumps({'result':'ok','session_id':'s','usage':{}}))\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        runner = ClaudeRunner(str(fake), None, 30, 30, self.state, True)
        runner.run("hi", self.ws, "admin", None, None, {"workspace": None})
        argv = json.loads(out.read_text())
        rules = json.loads(argv[argv.index("--settings") + 1])["permissions"]["ask"]
        self.assertEqual(rules, [f"{t}(~/.config/workspace-os/**)" for t in ("Read", "Glob", "Grep")])
        runner.run("hi", self.ws, "chat", None, None, {"workspace": "inv"})
        self.assertNotIn("--settings", json.loads(out.read_text()))


class PluginNameTests(Base):
    def test_plugin_cannot_claim_the_built_in_name_or_a_taken_one(self):
        plugins = Plugins(self.state, self.root / "plugins")
        impostor = make_plugin(self.root, "impostor")
        (impostor / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "wos"}))
        with self.assertRaisesRegex(RegistryError, "already in use"):
            plugins.add("impostor", impostor)
        first, second = make_plugin(self.root, "first"), make_plugin(self.root, "second")
        (second / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "first"}))
        plugins.add("first", first)
        with self.assertRaisesRegex(RegistryError, "already in use"):
            plugins.add("second", second)


class MessageTests(Base):
    def test_admin_command_errors_say_what_is_wrong(self):
        from tests.test_reception import FakeChannel, FakeRunner
        cfg = cfg_mod.Config("t", 42, self.root / "chat", self.state, None, 1, "claude", 60, 60, self.root / "s.yaml",
                             self.root / "repo", self.root / "ws", self.registry.path, admin_dir=self.root / "admin")
        chan = FakeChannel()
        gw = Gateway(cfg, chan, FakeRunner())
        say = lambda text: (gw.handle(Message("-1:0", -1, None, 42, text, 1, "supergroup", "G")), chan.sent[-1])[1]
        say("/admin")
        self.assertIn("between 1 and 4", say("/limits 99"))
        self.assertNotIn("/bind", say("/limits 99"))
        self.assertIn("Usage: /bind", say("/bind nonsense"))
        self.assertIn("Usage: /bind", say("/bind"))


if __name__ == "__main__":
    unittest.main()
