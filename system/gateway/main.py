"""Gateway: Telegram in, Claude Code out. Chat mode only (step 1)."""
import logging
import threading
import time
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as cfg_mod
from . import systemd
from .scheduler import Entry, Scheduler
from .runner import ClaudeRunner
from .state import Offset, Sessions, write_run_log
from .telegram import Message, TelegramChannel

log = logging.getLogger("gateway")


class Gateway:
    def __init__(self, cfg: cfg_mod.Config, channel: TelegramChannel, runner: ClaudeRunner):
        self.cfg, self.channel, self.runner = cfg, channel, runner
        self.sessions = Sessions(cfg.state_dir)
        self.offset = Offset(cfg.state_dir)
        self.locks: dict[str, threading.Lock] = defaultdict(threading.Lock)  # one run per conversation
        self.pool = ThreadPoolExecutor(max_workers=cfg.max_runs)  # concurrency cap
        self.scheduler = Scheduler(cfg.schedules_path, cfg.state_dir, self.fire_schedule, self.watchdog_ping)
        self.last_poll = time.time()

    def handle(self, msg: Message) -> None:
        text = msg.text.strip()
        if self.cfg.allowed_user_id is None:  # first-time setup: only reveal the user ID
            if text == "/id":
                self.channel.notify(msg.chat_id, f"Your user ID: {msg.user_id}", msg.thread_id)
            return
        if not self.channel.is_allowed(msg.user_id):
            log.warning("ignored message from user %s", msg.user_id)
            return
        if text in ("/start", "/help"):
            self.channel.notify(msg.chat_id, "Ready. Send a message. /new starts a fresh conversation.", msg.thread_id)
        elif text == "/new":
            self.sessions.clear(msg.key)
            self.channel.notify(msg.chat_id, "Fresh conversation started.", msg.thread_id)
        elif text == "/schedules":
            lines = self.scheduler.status(datetime.now())
            self.channel.notify(msg.chat_id, "\n".join(lines) or "No schedules.", msg.thread_id)
        elif text.startswith(("/stop ", "/resume ")):
            command, _, entry_id = text.partition(" ")
            ok = self.scheduler.set_paused(entry_id.strip(), command == "/stop")
            reply = f"{'Paused' if command == '/stop' else 'Resumed'} {entry_id.strip()}." if ok else "No such schedule."
            self.channel.notify(msg.chat_id, reply, msg.thread_id)
        elif text == "/id":
            self.channel.notify(msg.chat_id, f"Your user ID: {msg.user_id}", msg.thread_id)
        else:
            self.pool.submit(self.run_chat, msg)

    def run_chat(self, msg: Message) -> None:
        with self.locks[msg.key]:
            self.channel.typing(msg.chat_id, msg.thread_id)
            result = self.runner.run(msg.text, self.cfg.chat_dir, "chat", self.sessions.get(msg.key))
            if result.session_id and result.status == "ok":
                self.sessions.set(msg.key, result.session_id)
            write_run_log(self.cfg.state_dir, msg.key, "chat", result)
            self.channel.notify(msg.chat_id, result.text, msg.thread_id)

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
            result = self.runner.run(str(entry.run["prompt"]), cwd, "chat")  # fresh session each time
            write_run_log(self.cfg.state_dir, key, "chat", result)
            self.channel.notify(chat_id, result.text)

    def watchdog_ping(self) -> None:
        """Tell systemd we are alive, but only while the Telegram loop is also alive."""
        if time.time() - self.last_poll < 150:
            systemd.notify("WATCHDOG=1")

    def serve(self) -> None:
        log.info("gateway started")
        self.scheduler.start()
        systemd.notify("READY=1")
        for msg, next_offset in self.channel.poll(self.offset.get(), self.mark_polling):
            self.offset.set(next_offset)  # at-most-once: never replay a message after a crash
            try:
                self.handle(msg)
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
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = cfg_mod.load()
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Add it to ~/.config/workspace-os/env")
    if cfg.allowed_user_id is None:
        log.warning("TELEGRAM_ALLOWED_USER_ID not set: send /id to the bot, then add the number to the env file")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    ensure_chat_dir(cfg.chat_dir)
    Gateway(
        cfg, TelegramChannel(cfg.telegram_token, cfg.allowed_user_id),
        ClaudeRunner(cfg.claude_bin, cfg.model, cfg.run_timeout),
    ).serve()


if __name__ == "__main__":
    main()
