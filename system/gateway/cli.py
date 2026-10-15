"""bin/wos: the admin helper. Reception's agent uses this and nothing else to change the system.

  bin/wos workspace add NAME [--repo https://github.com/owner/repo ...]
  bin/wos workspace list
  bin/wos groups
  bin/wos bind CHAT_ID NAME
  bin/wos unbind NAME
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import config as cfg_mod
from .registry import Chats, Registry, RegistryError
from . import ghwrap, jobs, outbox, tokens
from .mcp import McpError, WorkspaceMcp
from .plugins import Plugins, available_agents
from .permissions import CAPABILITIES, Permissions
from .tasks import STATUSES, TaskError, TaskStore, detail, parse_duration, summarize
from .workspace import create_workspace


def describe_groups(chats: Chats, registry: Registry) -> list[str]:
    lines = []
    for chat_id, info in chats.known().items():
        found = registry.by_chat(chat_id)
        role = "Reception" if chat_id == chats.admin() else f"-> {found[0]}" if found else "not linked"
        lines.append(f"{chat_id} | {info['title'] or info['type']} | {role}")
    return lines


def add_task_parser(sub) -> None:
    task = sub.add_parser("task")
    task.add_argument("--task", type=int, default=int(os.environ["WOS_TASK_ID"]) if os.environ.get("WOS_TASK_ID") else None,
                      help="task id (default: $WOS_TASK_ID)")
    t = task.add_subparsers(dest="action", required=True)
    create = t.add_parser("create")
    create.add_argument("--workspace", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--desc", default="")
    create.add_argument("--repo", action="append", default=[])
    lst = t.add_parser("list")
    lst.add_argument("--workspace")
    lst.add_argument("--all", action="store_true")
    for name in ("show", "claim"):
        t.add_parser(name)
    t.add_parser("next").add_argument("--workspace")
    t.add_parser("comment").add_argument("text")
    t.add_parser("status").add_argument("state", choices=STATUSES)
    link = t.add_parser("link")
    link.add_argument("url")
    link.add_argument("--label", default="")
    resume = t.add_parser("resume")
    resume.add_argument("--in", dest="wait", required=True)
    resume.add_argument("--every")
    resume.add_argument("--check")
    resume.add_argument("--when-job-done", action="store_true")
    resume.add_argument("--max-checks", type=int, default=50)
    resume.add_argument("--reason", default="")
    t.add_parser("notify").add_argument("text")
    job = t.add_parser("job").add_subparsers(dest="job_action", required=True)
    start = job.add_parser("start")
    start.add_argument("argv", nargs=argparse.REMAINDER)
    job.add_parser("status")
    job.add_parser("stop")


def run_task_command(args, cfg, registry) -> None:
    store = TaskStore(cfg.state_dir)
    if args.action == "create":
        task = store.create(args.workspace, args.title, args.desc, args.repo, source="cli")
        print(summarize(task))
        return
    if args.action == "list":
        statuses = None if args.all else ("ready", "running", "waiting", "review", "blocked")
        print("\n".join(summarize(t) for t in store.list(args.workspace, statuses)) or "No tasks.")
        return
    if args.action == "next":
        task = store.next_ready(args.workspace)
        print(summarize(task) if task else "No ready tasks.")
        return
    if args.task is None:
        raise TaskError("which task? pass --task ID (inside an agent run it is set for you)")
    tid = args.task
    if args.action == "show":
        print(detail(store.get(tid)))
    elif args.action == "claim":
        print(summarize(store.claim(tid)))
    elif args.action == "comment":
        store.comment(tid, args.text)
        print("ok")
    elif args.action == "status":
        task = store.set_status(tid, args.state)
        if args.state in ("review", "blocked", "done"):
            outbox.enqueue(cfg.state_dir, f"Task #{tid} {args.state}: {task['title']}", task.get("notify"))
        print(summarize(task))
    elif args.action == "link":
        store.link(tid, args.url, args.label)
        print("ok")
    elif args.action == "resume":
        wait = parse_duration(args.wait)
        every = parse_duration(args.every) if args.every else None
        task = store.set_wake(tid, wait, every, args.check, args.when_job_done, args.max_checks, args.reason)
        print(f"waiting until {task['wake']['at']}")
    elif args.action == "notify":
        outbox.enqueue(cfg.state_dir, f"#{tid}: {args.text}", store.get(tid).get("notify"))
        print("queued")
    elif args.action == "job":
        task = store.get(tid)
        if args.job_action == "start":
            argv = [a for a in args.argv if a != "--"] if args.argv[:1] == ["--"] else args.argv
            entry = registry.all().get(task["workspace"])
            base = Path(entry["path"]) if entry else cfg.chat_dir
            task_dir = base / ".worktrees" / f"t{tid}"
            unit = jobs.start(cfg.state_dir, tid, argv, task_dir if task_dir.exists() else base, cfg.job_memory, cfg.job_cpu)
            store.add_job(tid, unit)
            print(f"started {unit}")
        elif args.job_action == "status":
            print("\n".join(json.dumps(jobs.status(cfg.state_dir, tid, u)) for u in task["jobs"]) or "No jobs.")
        else:
            for unit in task["jobs"]:
                jobs.stop(unit)
            print("stopped")


def agent_blocked(cfg) -> bool:
    """True inside an ordinary agent run. The admin agent acts for the owner after a card, and proves it with a
    live per-run token, so setting WOS_ADMIN by hand does nothing."""
    return bool(os.environ.get("WOS_AGENT")) and not tokens.valid(cfg.state_dir, os.environ.get("WOS_ADMIN"))


def workspace_path(registry, name: str | None) -> Path | None:
    entry = registry.all().get(name) if name else None
    return Path(entry["path"]) if entry else None


def run_workspace_agent(args, cfg, registry) -> None:
    if args.name not in registry.all():
        raise RegistryError(f"no workspace named {args.name!r}")
    current = registry.all()[args.name].get("agent", "default")
    if args.agent is None:
        print(f"{args.name}: {current}")
        return
    if agent_blocked(cfg):
        raise ValueError("only the owner can change a workspace's agent")
    if args.agent != "default" and args.agent not in available_agents(Plugins(cfg.state_dir, cfg.plugins_dir), workspace_path(registry, args.name)):
        raise RegistryError(f"no agent named {args.agent!r}. See: bin/wos agents {args.name}")
    registry.set_agent(args.name, None if args.agent == "default" else args.agent)
    print(f"{args.name} now uses {args.agent}.")


def run_plugin_command(args, cfg) -> None:
    plugins = Plugins(cfg.state_dir, cfg.plugins_dir)
    if args.action == "list":
        print("\n".join(plugins.listing()))
        return
    if agent_blocked(cfg):
        raise ValueError("only the owner can install or remove plugins")
    if args.action == "add":
        plugins.add(args.name, Path(args.path))
        print(f"added {args.name}")
    elif args.action == "install":
        print(f"installed {plugins.install(args.url, args.name)}")
    else:
        plugins.remove(args.name)
        print(f"removed {args.name}")


def run_limits_command(cfg) -> None:
    print(f"max_runs={cfg.max_runs} (set WOS_MAX_RUNS in the env file and restart the gateway to change it)")


def launch_admin(cfg, extra: list[str]) -> None:
    """Start the admin agent in this terminal. Claude's own prompts do the asking."""
    if os.environ.get("WOS_AGENT"):
        raise ValueError("the admin agent can only be started by the owner, in a terminal")
    from .runner import WOS_BIN
    plugins = Plugins(cfg.state_dir, cfg.plugins_dir)
    cmd = [cfg.claude_bin, "--agent", "wos:admin", *[a for d in plugins.dirs() for a in ("--plugin-dir", str(d))],
           *(extra[1:] if extra[:1] == ["--"] else extra)]
    os.environ["WOS_BIN"] = str(WOS_BIN)
    os.chdir(cfg.admin_dir)
    os.execvp(cmd[0], cmd)


def run_perms_command(args, cfg, registry) -> None:
    """Changing permissions is for the owner only. An ordinary agent run sets WOS_AGENT and is refused here."""
    perms = Permissions(cfg.state_dir, registry)
    if args.action != "show" and agent_blocked(cfg):
        raise ValueError("only the user can change permissions (use /grant in Reception)")
    if args.action == "show":
        names = [args.workspace] if args.workspace else list(registry.all())
        for name in names:
            shown = perms.show(name)
            print(f"{name}: capabilities={shown['capabilities']} rules={shown['allow']}")
        print("capabilities: " + ", ".join(CAPABILITIES))
        return
    if args.workspace not in registry.all() and not (args.workspace == "_admin" and args.action in ("rule", "unrule")):
        raise RegistryError(f"no workspace named {args.workspace!r}")
    {"grant": perms.grant, "revoke": perms.revoke, "rule": perms.add_rule, "unrule": perms.remove_rule}[args.action](
        args.workspace, args.value)
    print("ok")


def run_gh_command(args, cfg, registry) -> int:
    import subprocess
    if not args.workspace or args.workspace not in registry.all():
        raise ghwrap.GhDenied("name the workspace: bin/wos gh --workspace NAME -- <gh arguments>")
    gh_args = args.args[1:] if args.args[:1] == ["--"] else args.args
    checked = ghwrap.vet(gh_args, args.workspace, Permissions(cfg.state_dir, registry), Path.cwd())
    return subprocess.run(["gh", *checked]).returncode


def run_mcp_command(args, cfg, registry) -> None:
    store = WorkspaceMcp(cfg.state_dir)
    if args.action == "list":
        print(json.dumps(store.servers(args.workspace), indent=2))
        return
    if agent_blocked(cfg) and "mcp_manage" not in Permissions(cfg.state_dir, registry).caps(args.workspace):
        raise ValueError(f"{args.workspace!r} may not manage MCP servers. The user can allow it with /grant {args.workspace} mcp_manage")
    if args.action == "add":
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        if not command:
            raise McpError("give the command after --, for example: -- npx some-mcp-server")
        store.add(args.workspace, args.name, command[0], command[1:])
    else:
        store.remove(args.workspace, args.name)
    print("ok")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bin/wos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ws = sub.add_parser("workspace").add_subparsers(dest="action", required=True)
    add = ws.add_parser("add")
    add.add_argument("name")
    add.add_argument("--repo", action="append", default=[])
    ws.add_parser("list")
    ws_agent = ws.add_parser("agent", help="show or set the agent that answers in a workspace")
    ws_agent.add_argument("name")
    ws_agent.add_argument("agent", nargs="?", help="agent name, or 'default'")
    sub.add_parser("groups")
    bind = sub.add_parser("bind")
    bind.add_argument("chat_id", type=int)
    bind.add_argument("name")
    sub.add_parser("unbind").add_argument("name")
    add_task_parser(sub)
    sub.add_parser("agents").add_argument("workspace", nargs="?")
    plugin = sub.add_parser("plugin").add_subparsers(dest="action", required=True)
    plugin.add_parser("list")
    plugin_add = plugin.add_parser("add")
    plugin_add.add_argument("name")
    plugin_add.add_argument("path")
    plugin_install = plugin.add_parser("install")
    plugin_install.add_argument("url")
    plugin_install.add_argument("--name")
    plugin.add_parser("remove").add_argument("name")
    sub.add_parser("limits", help="show how many runs go at once")
    sub.add_parser("admin", help="start the admin agent in this terminal").add_argument("claude_args", nargs=argparse.REMAINDER)
    perms = sub.add_parser("perms").add_subparsers(dest="action", required=True)
    perms.add_parser("show").add_argument("workspace", nargs="?")
    for name in ("grant", "revoke", "rule", "unrule"):
        p = perms.add_parser(name)
        p.add_argument("workspace")
        p.add_argument("value")
    gh = sub.add_parser("gh", help="GitHub, limited to this workspace's repos")
    gh.add_argument("--workspace", default=os.environ.get("WOS_WORKSPACE") or None)
    gh.add_argument("args", nargs=argparse.REMAINDER)
    mcp = sub.add_parser("mcp").add_subparsers(dest="action", required=True)
    mcp.add_parser("list").add_argument("workspace")
    mcp_add = mcp.add_parser("add")
    mcp_add.add_argument("workspace")
    mcp_add.add_argument("name")
    mcp_add.add_argument("command", nargs=argparse.REMAINDER)
    mcp_rm = mcp.add_parser("remove")
    mcp_rm.add_argument("workspace")
    mcp_rm.add_argument("name")
    args = ap.parse_args(argv)

    cfg = cfg_mod.load()
    registry, chats = Registry(cfg.registry_path), Chats(cfg.state_dir)
    try:
        # Creating workspaces and linking groups is the admin agent's job. Checked here too, so wrapping the
        # call in `bash -c` does not get an ordinary agent past the permission check.
        if (args.cmd in ("bind", "unbind") or (args.cmd == "workspace" and args.action == "add")) and agent_blocked(cfg):
            raise ValueError("only the admin agent can create workspaces or link groups")
        if args.cmd == "workspace" and args.action == "add":
            created = create_workspace(args.name, args.repo, cfg.workspaces_dir, registry)
            Permissions(cfg.state_dir, registry).seed(args.name)       # default capabilities
            print(json.dumps(created, indent=2))
        elif args.cmd == "task":
            run_task_command(args, cfg, registry)
        elif args.cmd == "perms":
            run_perms_command(args, cfg, registry)
        elif args.cmd == "agents":
            print("\n".join(available_agents(Plugins(cfg.state_dir, cfg.plugins_dir), workspace_path(registry, args.workspace))) or "none")
        elif args.cmd == "plugin":
            run_plugin_command(args, cfg)
        elif args.cmd == "limits":
            run_limits_command(cfg)
        elif args.cmd == "admin":
            launch_admin(cfg, args.claude_args)
        elif args.cmd == "gh":
            return run_gh_command(args, cfg, registry)
        elif args.cmd == "mcp":
            run_mcp_command(args, cfg, registry)
        elif args.cmd == "workspace" and args.action == "agent":
            run_workspace_agent(args, cfg, registry)
        elif args.cmd == "workspace":
            for name, entry in registry.all().items():
                print(f"{name} | {entry['path']} | group {entry.get('telegram_chat', '-')} | agent {entry.get('agent', 'default')}")
        elif args.cmd == "groups":
            print("\n".join(describe_groups(chats, registry)) or "No groups seen yet. Send a message in the group first.")
        elif args.cmd == "bind":
            registry.bind(args.name, args.chat_id)
            print(f"Linked group {args.chat_id} to {args.name}.")
        elif args.cmd == "unbind":
            registry.unbind(args.name)
            print(f"Unlinked {args.name}.")
    except (RegistryError, TaskError, jobs.JobError, McpError, ghwrap.GhDenied, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
