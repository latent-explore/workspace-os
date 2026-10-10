"""bin/wos: the admin helper. Reception's agent uses this and nothing else to change the system.

  bin/wos workspace add NAME [--repo https://github.com/owner/repo ...]
  bin/wos workspace list
  bin/wos groups
  bin/wos bind CHAT_ID NAME
  bin/wos unbind NAME
"""
import argparse
import json
import sys

from . import config as cfg_mod
from .registry import Chats, Registry, RegistryError
from .workspace import create_workspace


def describe_groups(chats: Chats, registry: Registry) -> list[str]:
    lines = []
    for chat_id, info in chats.known().items():
        found = registry.by_chat(chat_id)
        role = "Reception" if chat_id == chats.admin() else f"-> {found[0]}" if found else "not linked"
        lines.append(f"{chat_id} | {info['title'] or info['type']} | {role}")
    return lines


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
    args = ap.parse_args(argv)

    cfg = cfg_mod.load()
    registry, chats = Registry(cfg.registry_path), Chats(cfg.state_dir)
    try:
        if args.cmd == "workspace" and args.action == "add":
            print(json.dumps(create_workspace(args.name, args.repo, cfg.workspaces_dir, registry), indent=2))
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
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
