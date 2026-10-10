"""Gateway: Telegram in, Claude Code out. Chat mode only (step 1)."""
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from . import config as cfg_mod
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

    def serve(self) -> None:
        log.info("gateway started")
        for msg, next_offset in self.channel.poll(self.offset.get()):
            self.offset.set(next_offset)  # at-most-once: never replay a message after a crash
            try:
                self.handle(msg)
            except Exception:
                log.exception("failed handling message")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = cfg_mod.load()
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing. Add it to ~/.config/workspace-os/env")
    if cfg.allowed_user_id is None:
        log.warning("TELEGRAM_ALLOWED_USER_ID not set: send /id to the bot, then add the number to the env file")
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    Gateway(
        cfg, TelegramChannel(cfg.telegram_token, cfg.allowed_user_id),
        ClaudeRunner(cfg.claude_bin, cfg.model, cfg.run_timeout),
    ).serve()


if __name__ == "__main__":
    main()
