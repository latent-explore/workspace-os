import json
import os
import stat
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from gateway import cli, tokens
from gateway import config as cfg_mod
from gateway.limits import Limits, RunGate
from gateway.main import Gateway
from gateway.permissions import Permissions
from gateway.plugins import Plugins, available_agents, frontmatter_name, plugin_agents, workspace_agents
from gateway.registry import Registry, RegistryError
from gateway.runner import BUILTIN_PLUGIN, ClaudeRunner
from gateway.telegram import Message

ME = 42


def make_plugin(root: Path, name: str, agents=("helper",)) -> Path:
    plugin = root / name
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name, "version": "1"}))
    (plugin / "agents").mkdir()
    for agent in agents:
        (plugin / "agents" / f"{agent}.md").write_text(f"---\nname: {agent}\ndescription: test\n---\nBody\n")
    return plugin


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.registry = Registry(self.state / "reg.yaml")
        self.ws = self.root / "ws" / "inv"
        self.ws.mkdir(parents=True)
        self.registry.add("inv", self.ws)
        self.perms = Permissions(self.state, self.registry)


class BuiltinPluginTests(unittest.TestCase):
    def test_builtin_plugin_ships_admin_and_workspace_agents(self):
        self.assertEqual(plugin_agents(BUILTIN_PLUGIN), ["wos:admin", "wos:workspace"])

    def test_agent_files_have_names_and_descriptions(self):
        for path in (BUILTIN_PLUGIN / "agents").glob("*.md"):
            text = path.read_text()
            self.assertTrue(text.startswith("---"), path)
            self.assertIn("description:", text.split("---")[1], path)
            self.assertEqual(frontmatter_name(path), path.stem)


class RunnerTests(Base):
    def setUp(self):
        super().setUp()
        self.out = self.root / "seen.json"
        fake = self.root / "claude"
        fake.write_text("#!/usr/bin/env python3\nimport json,sys,os\n"
                        f"json.dump({{'argv': sys.argv[1:], 'env': {{k: os.environ.get(k) for k in ('WOS_ADMIN','WOS_AGENT','WOS_BIN','WOS_WORKSPACE')}}}}, open('{self.out}', 'w'))\n"
                        "sys.stdin.read(); print(json.dumps({'result':'ok','session_id':'s','usage':{}}))\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.fake = str(fake)

    def run_it(self, mode, ctx=None, approvals=True, plugins=None):
        runner = ClaudeRunner(self.fake, None, 30, 30, self.state, approvals, None, 60, False, plugins)
        runner.run("hi", self.ws, mode, None, None, ctx)
        return json.loads(self.out.read_text())

    @staticmethod
    def flag(argv, name):
        return argv[argv.index(name) + 1]

    def test_each_mode_starts_its_agent(self):
        ctx = {"workspace": "inv", "notify": None}
        self.assertEqual(self.flag(self.run_it("chat", ctx)["argv"], "--agent"), "wos:workspace")
        self.assertEqual(self.flag(self.run_it("task", ctx)["argv"], "--agent"), "wos:workspace")
        self.assertEqual(self.flag(self.run_it("admin", {"workspace": None})["argv"], "--agent"), "wos:admin")

    def test_workspace_can_pick_another_agent(self):
        seen = self.run_it("chat", {"workspace": "inv", "agent": "email-triage"})
        self.assertEqual(self.flag(seen["argv"], "--agent"), "email-triage")

    def test_builtin_plugin_is_always_loaded_and_installed_ones_follow(self):
        extra = make_plugin(self.root, "extra")
        plugins = Plugins(self.state, self.root / "plugins")
        plugins.add("extra", extra)
        argv = self.run_it("chat", {"workspace": "inv"}, plugins=plugins)["argv"]
        dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--plugin-dir"]
        self.assertEqual(dirs, [str(BUILTIN_PLUGIN), str(extra.resolve())])

    def test_task_runs_get_the_task_skill_appended(self):
        argv = self.run_it("task", {"workspace": "inv"})["argv"]
        self.assertIn("Task skill", self.flag(argv, "--append-system-prompt"))
        self.assertNotIn("--append-system-prompt", self.run_it("chat", {"workspace": "inv"})["argv"])

    def test_admin_has_no_hard_denies_but_others_do(self):
        admin = self.run_it("admin", {"workspace": None})
        self.assertNotIn("--disallowedTools", admin["argv"])
        self.assertRegex(admin["env"]["WOS_ADMIN"], r"^[0-9a-f]{32}$")                 # a one-run token, not a flag
        self.assertEqual(list((self.state / "run-tokens").glob("*")), [])              # and it died with the run
        other = self.run_it("chat", {"workspace": "inv"})
        self.assertIn("Bash(gh:*)", self.flag(other["argv"], "--disallowedTools"))
        self.assertIsNone(other["env"]["WOS_ADMIN"])
        self.assertEqual(other["env"]["WOS_AGENT"], "1")

    def test_only_reads_are_pre_allowed_with_approvals(self):
        argv = self.run_it("admin", {"workspace": None})["argv"]
        self.assertEqual(self.flag(argv, "--allowedTools"), "Read,Glob,Grep")
        self.assertEqual(self.flag(argv, "--permission-prompt-tool"), "mcp__wos__approve")

    def test_without_approvals_fixed_lists_apply_and_wos_bin_is_set(self):
        seen = self.run_it("chat", None, approvals=False)
        self.assertEqual(self.flag(seen["argv"], "--allowedTools"), "Read,Glob,Grep")
        self.assertNotIn("--mcp-config", seen["argv"])
        self.assertTrue(seen["env"]["WOS_BIN"].endswith("bin/wos"))


class PluginTests(Base):
    def setUp(self):
        super().setUp()
        self.plugins = Plugins(self.state, self.root / "plugins")

    def test_add_list_remove(self):
        extra = make_plugin(self.root, "extra", ("one", "two"))
        self.plugins.add("extra", extra)
        self.assertEqual(self.plugins.agents()[-2:], ["extra:one", "extra:two"])
        self.assertIn("extra: extra:one, extra:two", self.plugins.listing()[1])
        self.plugins.remove("extra")
        self.assertEqual(self.plugins.dirs(), [BUILTIN_PLUGIN])

    def test_validation(self):
        extra = make_plugin(self.root, "extra")
        for name in ("wos", "Bad Name", ""):
            with self.assertRaises(RegistryError, msg=name):
                self.plugins.add(name, extra)
        with self.assertRaises(RegistryError):
            self.plugins.add("x", self.root)                   # not a plugin
        self.plugins.add("extra", extra)
        with self.assertRaises(RegistryError):
            self.plugins.add("extra", extra)                   # twice
        with self.assertRaises(RegistryError):
            self.plugins.remove("ghost")

    def test_install_clones_then_registers_and_cleans_up_on_failure(self):
        source = make_plugin(self.root / "src", "cool")

        def clone(url, dest):
            import shutil
            shutil.copytree(source, dest)

        name = self.plugins.install("https://github.com/o/cool", clone=clone)
        self.assertEqual(name, "cool")
        self.assertIn("cool:helper", self.plugins.agents())
        with self.assertRaises(RegistryError):
            self.plugins.install("https://github.com/o/cool", clone=clone)           # already there
        broken = lambda url, dest: dest.mkdir(parents=True)                           # clones something that is no plugin
        with self.assertRaises(RegistryError):
            self.plugins.install("https://github.com/o/junk", clone=broken)
        self.assertFalse((self.root / "plugins" / "junk").exists())
        with self.assertRaises(RegistryError):
            self.plugins.install("https://evil.com/o/x", clone=clone)

    def test_vanished_plugin_folders_are_skipped(self):
        extra = make_plugin(self.root, "extra")
        self.plugins.add("extra", extra)
        import shutil
        shutil.rmtree(extra)
        self.assertEqual(self.plugins.dirs(), [BUILTIN_PLUGIN])

    def test_workspace_own_agents(self):
        (self.ws / ".claude" / "agents").mkdir(parents=True)
        (self.ws / ".claude" / "agents" / "email-triage.md").write_text("---\nname: email-triage\n---\nx\n")
        self.assertEqual(workspace_agents(self.ws), ["email-triage"])
        self.assertIn("email-triage", available_agents(self.plugins, self.ws))
        self.assertNotIn("email-triage", available_agents(self.plugins, None))


class LimitsTests(Base):
    def test_default_set_clamp(self):
        limits = Limits(self.state, 1, 4)
        self.assertEqual(limits.max_runs(), 1)
        limits.set_max_runs(3)
        self.assertEqual(Limits(self.state, 1, 4).max_runs(), 3)          # survives a restart
        for bad in (0, 5, -1):
            with self.assertRaises(ValueError):
                limits.set_max_runs(bad)
        (self.state / "limits.yaml").write_text("max_runs: 99\n")
        self.assertEqual(limits.max_runs(), 4)                             # a hand-edited file cannot beat the hard maximum

    def test_gate_enforces_the_cap_and_follows_changes(self):
        limits = Limits(self.state, 1, 4)
        gate = RunGate(limits)
        running, peak, lock = [0], [0], threading.Lock()

        def job():
            with gate:
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                time.sleep(0.15)
                with lock:
                    running[0] -= 1

        threads = [threading.Thread(target=job) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(peak[0], 1)
        limits.set_max_runs(3)
        peak[0] = 0
        threads = [threading.Thread(target=job) for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertGreaterEqual(peak[0], 2)
        self.assertLessEqual(peak[0], 3)
        self.assertEqual(gate.active, 0)


class AdminPermissionTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = {"workspace": None, "mode": "admin"}
        self.other = {"workspace": "inv", "mode": "chat"}

    def kind(self, ctx, tool, **inp):
        return self.perms.check(ctx, tool, inp).kind

    def test_reads_free_everything_else_asks(self):
        self.assertEqual(self.kind(self.admin, "Read", file_path="/etc/hostname"), "allow")
        self.assertEqual(self.kind(self.admin, "Write", file_path="/home/wos/x.txt"), "ask")
        self.assertEqual(self.kind(self.admin, "Bash", command="rm notes.txt"), "ask")

    def test_what_is_a_hard_deny_for_others_is_an_ask_for_admin(self):
        cases = [("Read", {"file_path": "/home/wos/.config/workspace-os/env"}),
                 ("Bash", {"command": "gh repo list"}),
                 ("Bash", {"command": "bin/wos perms grant inv web"}),
                 ("Bash", {"command": "$WOS_BIN plugin install https://github.com/o/p"}),
                 ("Bash", {"command": "$WOS_BIN limits set max_runs 2"}),
                 ("Bash", {"command": "$WOS_BIN workspace agent inv email-triage"})]
        for tool, inp in cases:
            self.assertEqual(self.perms.check(self.admin, tool, inp).kind, "ask", inp)
            self.assertEqual(self.perms.check(self.other, tool, inp).kind, "deny", inp)

    def test_read_only_wos_commands_are_free_for_everyone(self):
        for command in ("$WOS_BIN plugin list", "$WOS_BIN limits show", "$WOS_BIN agents", "$WOS_BIN workspace list",
                        "$WOS_BIN workspace add x --repo https://github.com/o/r"):
            self.assertEqual(self.perms.check(self.admin, "Bash", {"command": command}).kind, "allow", command)

    def test_always_allow_for_admin_saves_admin_scope_rules(self):
        rule = self.perms.rule_for(self.admin, "Bash", {"command": "mkdir build"})
        self.assertEqual(rule, {"kind": "rule", "value": "Bash(mkdir:*)"})
        self.perms.apply(self.admin, rule)
        self.assertEqual(self.perms.show("_admin")["allow"], ["Bash(mkdir:*)"])
        self.assertEqual(self.kind(self.admin, "Bash", command="mkdir other"), "allow")
        self.assertEqual(self.kind(self.other, "Bash", command="mkdir other"), "ask")      # does not leak to a workspace
        write = self.perms.rule_for(self.admin, "Write", {"file_path": "/x"})
        self.assertEqual(write, {"kind": "rule", "value": "Write"})
        self.perms.apply(self.admin, write)
        self.assertEqual(self.kind(self.admin, "Write", file_path="/home/wos/y"), "allow")

    def test_risky_things_are_never_always(self):
        for command in ("sudo ls", "git push origin x", "gh repo list", "$WOS_BIN perms grant inv web"):
            self.assertIsNone(self.perms.rule_for(self.admin, "Bash", {"command": command}), command)

    def test_workspace_grants_still_apply_to_admin_edits(self):
        self.perms.grant("inv", "edit_files")
        self.assertEqual(self.kind(self.admin, "Write", file_path=str(self.ws / "docs" / "a.md")), "allow")
        self.assertEqual(self.kind(self.admin, "Write", file_path="/etc/x"), "ask")

    def test_chat_without_workspace_gets_no_rules(self):
        ctx = {"workspace": None, "mode": "chat"}
        self.assertIsNone(self.perms.rule_for(ctx, "Bash", {"command": "ls"}))
        self.assertEqual(self.kind(ctx, "Bash", command="ls"), "ask")


class CliTests(Base):
    def run_cli(self, *argv, env=None):
        environ = {"WOS_STATE_DIR": str(self.state), "WOS_REGISTRY": str(self.registry.path),
                   "WOS_PLUGINS_DIR": str(self.root / "plugins"), "WOS_WORKSPACES_DIR": str(self.root / "ws")}
        out, err = StringIO(), StringIO()
        with mock.patch.dict(os.environ, {**environ, **(env or {})}), redirect_stdout(out), redirect_stderr(err):
            for key in ("WOS_AGENT", "WOS_ADMIN"):
                if key not in (env or {}):
                    os.environ.pop(key, None)
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_workspace_agent(self):
        self.assertEqual(self.run_cli("workspace", "agent", "inv")[1].strip(), "inv: default")
        code, out = self.run_cli("workspace", "agent", "inv", "wos:workspace")
        self.assertEqual(code, 0)
        self.assertEqual(self.registry.all()["inv"]["agent"], "wos:workspace")
        code, out = self.run_cli("workspace", "agent", "inv", "nonsense")
        self.assertEqual(code, 1)
        self.assertIn("no agent named", out)
        self.run_cli("workspace", "agent", "inv", "default")
        self.assertNotIn("agent", self.registry.all()["inv"])

    def test_ordinary_agents_are_blocked_but_the_admin_agent_is_not(self):
        for argv in (("workspace", "agent", "inv", "wos:admin"), ("limits", "set", "max_runs", "2"),
                     ("plugin", "remove", "x"), ("perms", "grant", "inv", "web")):
            code, out = self.run_cli(*argv, env={"WOS_AGENT": "1"})
            self.assertEqual(code, 1, argv)
            self.assertIn("only the", out)
        token = tokens.issue(self.state)                                            # what the runner gives an admin run
        code, _ = self.run_cli("limits", "set", "max_runs", "2", env={"WOS_AGENT": "1", "WOS_ADMIN": token})
        self.assertEqual(code, 0)
        code, _ = self.run_cli("perms", "grant", "inv", "web", env={"WOS_AGENT": "1", "WOS_ADMIN": token})
        self.assertEqual(code, 0)
        self.assertIn("web", self.perms.caps("inv"))

    def test_limits(self):
        self.assertIn("max_runs=1", self.run_cli("limits", "show")[1])
        self.assertEqual(self.run_cli("limits", "set", "max_runs", "3")[0], 0)
        self.assertIn("max_runs=3", self.run_cli("limits", "show")[1])
        code, out = self.run_cli("limits", "set", "max_runs", "101")
        self.assertEqual(code, 1)
        self.assertIn("between 1 and 100", out)

    def test_plugin_add_list_remove_and_agents(self):
        extra = make_plugin(self.root, "extra")
        self.assertEqual(self.run_cli("plugin", "add", "extra", str(extra))[0], 0)
        self.assertIn("extra:helper", self.run_cli("plugin", "list")[1])
        self.assertIn("extra:helper", self.run_cli("agents")[1])
        self.assertEqual(self.run_cli("plugin", "remove", "extra")[0], 0)

    def test_admin_rules_can_be_set_by_the_owner(self):
        self.assertEqual(self.run_cli("perms", "rule", "_admin", "Bash(ls:*)")[0], 0)
        self.assertEqual(self.perms.show("_admin")["allow"], ["Bash(ls:*)"])
        self.assertEqual(self.run_cli("perms", "grant", "_admin", "web")[0], 1)       # capabilities need a real workspace


class GatewayCommandTests(Base):
    def setUp(self):
        super().setUp()
        from tests.test_reception import FakeChannel, FakeRunner
        self.cfg = cfg_mod.Config("t", ME, self.root / "chat", self.state, None, 1, "claude", 60, 60,
                                  self.root / "s.yaml", self.root / "repo", self.root / "ws", self.registry.path,
                                  admin_dir=self.root / "admin", plugins_dir=self.root / "plugins")
        self.chan, self.runner = FakeChannel(), FakeRunner()
        self.gw = Gateway(self.cfg, self.chan, self.runner)
        self.gw.handle(self.msg("/admin", -1))

    def msg(self, text, chat=-1, kind="supergroup"):
        return Message(f"{chat}:0", chat, None, ME, text, 1, kind, "G")

    def say(self, text, chat=-1):
        self.gw.handle(self.msg(text, chat))
        return self.chan.sent[-1]

    def test_agent_commands(self):
        self.assertIn("wos:admin", self.say("/agents"))
        self.assertIn("inv uses default", self.say("/agent inv"))
        self.assertIn("now uses wos:workspace", self.say("/agent inv wos:workspace"))
        self.assertEqual(self.registry.all()["inv"]["agent"], "wos:workspace")
        self.assertIn("no agent named", self.say("/agent inv nonsense"))
        self.assertIn("now uses default", self.say("/agent inv default"))
        self.assertIn("Usage", self.say("/agent ghost x"))

    def test_limits_and_plugins_commands(self):
        self.assertIn("Runs at once: 1", self.say("/limits"))
        self.assertIn("Runs at once: 3", self.say("/limits 3"))
        self.assertEqual(self.gw.limits.max_runs(), 3)
        self.assertIn("Error", self.say("/limits 101"))
        self.assertIn("wos (built-in)", self.say("/plugins"))

    def test_these_commands_work_only_in_reception(self):
        for command in ("/agent inv", "/agents", "/limits 2", "/plugins"):
            self.assertEqual(self.say(command, chat=-2), "Use this in Reception.", command)

    def test_private_chat_and_reception_are_the_admin_agent_and_a_group_uses_its_workspace_agent(self):
        self.assertEqual(self.gw.route(self.msg("x", 7, "private")), (self.cfg.admin_dir, "admin", None))
        self.assertEqual(self.gw.route(self.msg("x", -1)), (self.cfg.admin_dir, "admin", None))
        self.registry.bind("inv", -2)
        self.registry.set_agent("inv", "email-triage")
        seen = []
        self.runner.run = lambda prompt, cwd, mode, session_id=None, extra_env=None, ctx=None: seen.append((cwd, mode, ctx)) or __import__("gateway.runner", fromlist=["RunResult"]).RunResult("ok", "r", "s")
        self.gw.run_chat(self.msg("hello", -2))
        cwd, mode, ctx = seen[0]
        self.assertEqual((cwd, mode, ctx["workspace"], ctx["agent"]), (self.ws, "chat", "inv", "email-triage"))


if __name__ == "__main__":
    unittest.main()
