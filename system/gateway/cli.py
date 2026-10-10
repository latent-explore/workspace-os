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
from . import jobs, outbox
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="bin/wos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ws = sub.add_parser("workspace").add_subparsers(dest="action", required=True)
    add = ws.add_parser("add")
    add.add_argument("name")
    add.add_argument("--repo", action="append", default=[])
    ws.add_parser("list")
    sub.add_parser("groups")
    bind = sub.add_parser("bind")
    bind.add_argument("chat_id", type=int)
    bind.add_argument("name")
    sub.add_parser("unbind").add_argument("name")
    add_task_parser(sub)
    args = ap.parse_args(argv)

    cfg = cfg_mod.load()
    registry, chats = Registry(cfg.registry_path), Chats(cfg.state_dir)
    try:
        if args.cmd == "workspace" and args.action == "add":
            print(json.dumps(create_workspace(args.name, args.repo, cfg.workspaces_dir, registry), indent=2))
        elif args.cmd == "task":
            run_task_command(args, cfg, registry)
        elif args.cmd == "workspace":
            for name, entry in registry.all().items():
                print(f"{name} | {entry['path']} | group {entry.get('telegram_chat', '-')}")
        elif args.cmd == "groups":
            print("\n".join(describe_groups(chats, registry)) or "No groups seen yet. Send a message in the group first.")
        elif args.cmd == "bind":
            registry.bind(args.name, args.chat_id)
            print(f"Linked group {args.chat_id} to {args.name}.")
        elif args.cmd == "unbind":
            registry.unbind(args.name)
            print(f"Unlinked {args.name}.")
    except (RegistryError, TaskError, jobs.JobError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
