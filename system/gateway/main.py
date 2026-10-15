"""Gateway: Telegram in, Claude Code out."""
import logging
import os
import threading
import time
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as cfg_mod
from . import systemd
from .cli import describe_groups
from .dispatcher import Dispatcher
from .scheduler import Entry, Scheduler
from .registry import Chats, Registry, RegistryError
from .runner import ClaudeRunner
from .state import Offset, Sessions, write_run_log
from .tasks import TaskError, TaskStore, detail, summarize
from .workspace import workspace_repos
from .approvals import Approvals
from .cards import ApprovalCards
from .limits import Limits, RunGate
from .mcp import WorkspaceMcp
from .plugins import Plugins, available_agents
from .permissions import CAPABILITIES, Permissions
from .telegram import Callback, Message, TelegramChannel

log = logging.getLogger("gateway")
GROUP_TYPES = ("group", "supergroup")
ADMIN_ONLY = ("/groups", "/workspaces", "/bind", "/unbind", "/perms", "/grant", "/revoke", "/allow", "/unallow",
              "/agent", "/agents", "/limits", "/plugins")
TASK_COMMANDS = ("/tasks", "/task", "/addtask", "/cancel", "/retry", "/reply")


def normalize(text: str) -> str:
    """'/new@MyBot  x' -> '/new x'. Telegram adds the bot name to commands in groups."""
    text = text.strip()
    if text.startswith("/"):
        first, sep, rest = text.partition(" ")
        return first.split("@")[0] + sep + rest.strip()
    return text


class Gateway:
    def __init__(self, cfg: cfg_mod.Config, channel: TelegramChannel, runner: ClaudeRunner):
        self.cfg, self.channel, self.runner = cfg, channel, runner
        self.sessions = Sessions(cfg.state_dir)
        self.registry = Registry(cfg.registry_path)
        self.chats = Chats(cfg.state_dir)
        self.tasks = TaskStore(cfg.state_dir)
        self.perms = Permissions(cfg.state_dir, self.registry)
        self.approvals = Approvals(cfg.state_dir)
        self.cards = ApprovalCards(cfg, self.approvals, channel, self.perms)
        self.offset = Offset(cfg.state_dir)
        self.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # one run per conversation
        self.limits = Limits(cfg.state_dir, cfg.max_runs, cfg.hard_max_runs)
        self.gate = RunGate(self.limits)                               # the run cap, changeable without a restart
        self.plugins = Plugins(cfg.state_dir, cfg.plugins_dir)
        self.pool = ThreadPoolExecutor(max_workers=cfg.hard_max_runs)  # threads; the gate decides how many run
        self.scheduler = Scheduler(cfg.schedules_path, cfg.state_dir, self.fire_schedule, self.watchdog_ping)
        self.last_poll = time.time()
        self.dispatcher = Dispatcher(cfg, self.tasks, self.registry, runner, channel, self.pool, write_run_log,
                                     self.limits, self.gate)

    def reply(self, msg: Message, text: str) -> None:
        self.channel.notify(msg.chat_id, text, msg.thread_id)

    def route(self, msg: Message) -> tuple[Path, str, str | None] | None:
        """Where does this chat run, in which mode, for which workspace? My private chat and Reception:
        the admin agent in the wos home. A linked group: that workspace's folder. Anything else: nowhere."""
        if msg.chat_type == "private" or msg.chat_id == self.chats.admin():
            return self.cfg.admin_dir, "admin", None        # only the owner can reach either, so it is the admin agent
        found = self.registry.by_chat(msg.chat_id)
        return (Path(found[1]["path"]), "chat", found[0]) if found else None

    def handle(self, msg: Message) -> None:
        text = normalize(msg.text)
        log.debug("message: user=%s chat=%s topic=%s allowed=%s",
                  msg.user_id, msg.chat_id, msg.thread_id, self.channel.is_allowed(msg.user_id))
        if self.cfg.allowed_user_id is None:  # first-time setup: only reveal the user ID
            if text == "/id":
                self.reply(msg, f"Your user ID: {msg.user_id}")
            return
        if not self.channel.is_allowed(msg.user_id):
            log.warning("ignored message from user %s", msg.user_id)
            return
        if msg.chat_type in GROUP_TYPES:
            self.chats.see(msg.chat_id, msg.chat_title, msg.chat_type)
        cmd, _, arg = text.partition(" ")
        arg = arg.strip()
        if cmd == "/admin":
            self.cmd_admin(msg)
        elif cmd in ADMIN_ONLY:
            self.cmd_admin_only(msg, cmd, arg)
        elif cmd in ("/start", "/help"):
            self.reply(msg, "Ready. Send a message. /new starts a fresh conversation.")
        elif cmd == "/new":
            self.sessions.clear(msg.key)
            self.reply(msg, "Fresh conversation started.")
        elif cmd == "/schedules":
            self.reply(msg, "\n".join(self.scheduler.status(datetime.now())) or "No schedules.")
        elif cmd in ("/stop", "/resume") and arg:
            ok = self.scheduler.set_paused(arg, cmd == "/stop")
            self.reply(msg, f"{'Paused' if cmd == '/stop' else 'Resumed'} {arg}." if ok else "No such schedule.")
        elif cmd == "/id":
            self.reply(msg, f"Your user ID: {msg.user_id}")
        elif cmd in TASK_COMMANDS:
            self.task_command(msg, cmd, arg)
        else:
            self.pool.submit(self.run_chat, msg)

    def linked_workspace(self, msg: Message) -> str | None:
        found = self.registry.by_chat(msg.chat_id)
        return found[0] if found else None

    def task_command(self, msg: Message, cmd: str, arg: str) -> None:
        """Tasks from chat: /tasks, /task 7, /addtask title | details, /cancel 7, /retry 7, /reply 7 text."""
        try:
            ws = self.linked_workspace(msg)
            if cmd == "/tasks":
                open_states = ("ready", "running", "waiting", "review", "blocked")
                self.reply(msg, "\n".join(summarize(t) for t in self.tasks.list(ws, open_states)) or "No open tasks.")
            elif cmd == "/addtask":
                if ws is None:  # Reception or private chat: the first word names the workspace
                    ws, _, arg = arg.partition(" ")
                if ws not in self.registry.all():
                    raise TaskError("Use /addtask in a linked group, or: /addtask <workspace> <title>")
                title, _, details = arg.partition("|")
                target = {"chat_id": msg.chat_id, "thread_id": msg.thread_id}
                repos = workspace_repos(self.registry.all()[ws]["path"])
                task = self.tasks.create(ws, title, details, repos, target, source="chat")
                self.reply(msg, f"Added {summarize(task)}")
            else:
                task_id, _, rest = arg.partition(" ")
                if not task_id.isdigit():
                    raise TaskError(f"Usage: {cmd} <task id>" + (" <text>" if cmd == "/reply" else ""))
                task_id = int(task_id)
                if cmd == "/task":
                    self.reply(msg, detail(self.tasks.get(task_id)))
                elif cmd == "/cancel":
                    self.tasks.set_status(task_id, "cancelled")
                    self.reply(msg, f"Cancelled #{task_id}. A run already in progress finishes first.")
                elif cmd == "/retry":
                    self.tasks.set_status(task_id, "ready")
                    self.reply(msg, f"#{task_id} is ready again.")
                elif cmd == "/reply":
                    if not rest.strip():
                        raise TaskError("Usage: /reply <task id> <text>")
                    self.tasks.comment(task_id, rest, by="me")
                    self.tasks.set_status(task_id, "ready")
                    self.reply(msg, f"Sent to #{task_id}; it will pick it up.")
        except TaskError as exc:
            self.reply(msg, str(exc))

    def cmd_admin(self, msg: Message) -> None:
        if msg.chat_type not in GROUP_TYPES:
            self.reply(msg, "Send /admin inside the group that should be Reception.")
        elif self.chats.set_admin(msg.chat_id):
            self.reply(msg, "This group is now Reception. Admin work runs here, in the workspace-os folder.")
        else:
            self.reply(msg, "Reception is already another group.")

    def cmd_admin_only(self, msg: Message, cmd: str, arg: str) -> None:
        if msg.chat_id != self.chats.admin():
            self.reply(msg, "Use this in Reception.")
            return
        try:
            if cmd == "/groups":
                self.reply(msg, "\n".join(describe_groups(self.chats, self.registry)) or "No groups seen yet.")
            elif cmd == "/workspaces":
                lines = [f"{n}: {e['path']} (group {e.get('telegram_chat', '-')})" for n, e in self.registry.all().items()]
                self.reply(msg, "\n".join(lines) or "No workspaces yet.")
            elif cmd == "/bind":
                chat_id, _, name = arg.partition(" ")
                if not chat_id.lstrip("-").isdigit() or not name.strip():
                    raise ValueError("Usage: /bind <group id> <workspace>")
                self.registry.bind(name.strip(), int(chat_id))
                self.reply(msg, f"Linked group {chat_id} to {name.strip()}.")
            elif cmd == "/unbind":
                self.registry.unbind(arg)
                self.reply(msg, f"Unlinked {arg}.")
            elif cmd == "/perms":
                self.reply(msg, self.describe_permissions(arg))
            elif cmd == "/agents":
                path = self.registry.all().get(arg, {}).get("path") if arg else None
                self.reply(msg, "\n".join(available_agents(self.plugins, Path(path) if path else None)) or "No agents.")
            elif cmd == "/agent":
                workspace, _, name = arg.partition(" ")
                entry = self.registry.all().get(workspace)
                if entry is None:
                    raise RegistryError("Usage: /agent <workspace> [agent name | default]")
                name = name.strip()
                if not name:
                    self.reply(msg, f"{workspace} uses {entry.get('agent', 'default')}.")
                else:
                    if name != "default" and name not in available_agents(self.plugins, Path(entry["path"])):
                        raise RegistryError(f"no agent named {name!r}. See /agents {workspace}")
                    self.registry.set_agent(workspace, None if name == "default" else name)
                    self.reply(msg, f"{workspace} now uses {name}.")
            elif cmd == "/limits":
                if arg:
                    self.limits.set_max_runs(int(arg))
                self.reply(msg, f"Runs at once: {self.limits.max_runs()} (hard maximum {self.limits.hard_max}).")
            elif cmd == "/plugins":
                self.reply(msg, "\n".join(self.plugins.listing()))
            elif cmd in ("/allow", "/unallow"):
                workspace, _, rule = arg.partition(" ")
                if workspace not in self.registry.all() or not rule.strip():
                    raise RegistryError("Usage: /allow <workspace> <rule>, for example /allow email mcp__claude_ai_Gmail__*")
                (self.perms.add_rule if cmd == "/allow" else self.perms.remove_rule)(workspace, rule.strip())
                self.reply(msg, f"{'Allowed' if cmd == '/allow' else 'Removed'} {rule.strip()} for {workspace}.")
            elif cmd in ("/grant", "/revoke"):
                workspace, _, capability = arg.partition(" ")
                if workspace not in self.registry.all():
                    raise RegistryError(f"no workspace named {workspace!r}")
                (self.perms.grant if cmd == "/grant" else self.perms.revoke)(workspace, capability.strip())
                self.reply(msg, f"{'Granted' if cmd == '/grant' else 'Revoked'} {capability.strip()} for {workspace}.")
        except (RegistryError, ValueError) as exc:
            self.reply(msg, f"Error: {exc}")

    def describe_permissions(self, workspace: str) -> str:
        if not workspace:
            names = "\n".join(f"{name}: {why}" for name, why in CAPABILITIES.items())
            return f"Capabilities:\n{names}\n\nUse /perms <workspace>, /grant <workspace> <capability>, /revoke <workspace> <capability>, /allow <workspace> <rule>."
        if workspace not in self.registry.all():
            return f"No workspace named {workspace!r}."
        shown = self.perms.show(workspace)
        rules = ", ".join(shown["allow"]) or "none"
        return f"{workspace}\ncapabilities: {', '.join(shown['capabilities']) or 'none'}\nextra rules: {rules}"

    def run_chat(self, msg: Message) -> None:
        with self.locks[msg.key]:
            target = self.route(msg)
            if target is None:
                self.reply(msg, "This group is not linked to a workspace yet. Link it from Reception: /groups, then /bind.")
                return
            cwd, mode, workspace = target
            self.channel.typing(msg.chat_id, msg.thread_id)
            ctx = {"workspace": workspace, "notify": {"chat_id": msg.chat_id, "thread_id": msg.thread_id},
                   "agent": self.registry.all().get(workspace, {}).get("agent") if workspace else None}
            with self.gate:
                result = self.runner.run(normalize(msg.text), cwd, mode, self.sessions.get(msg.key), None, ctx)
            if result.session_id and result.status == "ok":
                self.sessions.set(msg.key, result.session_id)
            write_run_log(self.cfg.state_dir, msg.key, mode, result)
            self.reply(msg, result.text)

    def mark_polling(self) -> None:
        self.last_poll = time.time()

    def fire_schedule(self, entry: Entry) -> None:
        """Called by the scheduler. Plain messages cost nothing; prompts start an agent run."""
        chat_id = self.cfg.allowed_user_id  # a private chat has the same id as the user
        if chat_id is None:
            log.warning("schedule %s skipped: no allowed user", entry.id)
        elif "message" in entry.run:
            self.channel.notify(chat_id, str(entry.run["message"]))
        else:
            self.pool.submit(self.run_scheduled, entry, chat_id)

    def run_scheduled(self, entry: Entry, chat_id: int) -> None:
        key = f"sched:{entry.id}"
        with self.locks[key]:
            cwd = Path(entry.run.get("cwd") or self.cfg.chat_dir).expanduser()
            with self.gate:
                result = self.runner.run(str(entry.run["prompt"]), cwd, "chat", None, None, {"workspace": None, "notify": None})  # fresh session each time
            write_run_log(self.cfg.state_dir, key, "chat", result)
            self.channel.notify(chat_id, result.text)

    def watchdog_ping(self) -> None:
        """Tell systemd we are alive, but only while the Telegram loop is also alive."""
        if time.time() - self.last_poll < 150:
            systemd.notify("WATCHDOG=1")

    def serve(self) -> None:
        log.info("gateway started")
        self.scheduler.start()
        self.dispatcher.start()
        self.cards.start()
        systemd.notify("READY=1")
        for item, next_offset in self.channel.poll(self.offset.get(), self.mark_polling):
            self.offset.set(next_offset)  # at-most-once: never replay a message after a crash
            try:
                if isinstance(item, Callback):
                    self.cards.handle_callback(item)
                else:
                    self.handle(item)
            except Exception:
                log.exception("failed handling message")


CHAT_CLAUDE_MD = """# Chat

You are the user's assistant, talking over a phone chat.
Reply in at most 5 short lines, in plain language. Do not explore files unless asked.
"""


def ensure_chat_dir(chat_dir) -> None:
    """A small neutral folder, so chat does not load a repo's CLAUDE.md and docs."""
    chat_dir.mkdir(parents=True, exist_ok=True)
    marker = chat_dir / "CLAUDE.md"
    if not marker.exists():
        marker.write_text(CHAT_CLAUDE_MD)


def main() -> None:
    logging.basicConfig(level=os.environ.get("WOS_LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(message)s")
    cfg = cfg_mod.load()
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Add it to ~/.config/workspace-os/env")
    if cfg.allowed_user_id is None:
        log.warning("TELEGRAM_ALLOWED_USER_ID not set: send /id to the bot, then add the number to the env file")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    ensure_chat_dir(cfg.chat_dir)
    Gateway(
        cfg, TelegramChannel(cfg.telegram_token, cfg.allowed_user_id),
        ClaudeRunner(cfg.claude_bin, cfg.model, cfg.run_timeout, cfg.task_timeout, cfg.state_dir,
                     cfg.approvals, WorkspaceMcp(cfg.state_dir), cfg.approval_wait, cfg.strict_mcp,
                     Plugins(cfg.state_dir, cfg.plugins_dir)),
    ).serve()


if __name__ == "__main__":
    main()
