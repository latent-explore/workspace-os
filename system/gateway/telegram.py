"""Telegram channel: long polling over HTTPS. No open ports, no tokens spent."""
import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)
LIMIT = 4096


@dataclass
class Message:
    key: str  # chat:topic, identifies the conversation
    chat_id: int
    thread_id: int | None
    user_id: int
    text: str
    update_id: int
    chat_type: str = "private"
    chat_title: str = ""


def chunk(text: str, limit: int = LIMIT) -> list[str]:
    """Split on newlines where possible so no piece exceeds the limit."""
    text = text.strip() or "(empty reply)"
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        cut = cut if cut > limit // 2 else limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


@dataclass
class Callback:
    """A tap on an inline button."""
    id: str
    user_id: int
    chat_id: int
    message_id: int
    data: str
    update_id: int


def to_callback(update: dict) -> Callback | None:
    cb = update.get("callback_query")
    if not cb or not cb.get("data") or not cb.get("message"):
        return None
    return Callback(cb["id"], cb["from"]["id"], cb["message"]["chat"]["id"], cb["message"]["message_id"],
                    cb["data"], update["update_id"])


def to_message(update: dict) -> Message | None:
    msg = update.get("message")
    if not msg or not msg.get("text") or "from" not in msg:
        return None
    thread = msg.get("message_thread_id") if msg.get("is_topic_message") else None
    chat_id = msg["chat"]["id"]
    return Message(
        key=f"{chat_id}:{thread or 0}", chat_id=chat_id, thread_id=thread,
        user_id=msg["from"]["id"], text=msg["text"], update_id=update["update_id"],
        chat_type=msg["chat"].get("type", "private"), chat_title=msg["chat"].get("title", ""),
    )


class TelegramChannel:
    def __init__(self, token: str, allowed_user_id: int | None, session: requests.Session | None = None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.allowed_user_id = allowed_user_id
        self.http = session or requests.Session()

    def is_allowed(self, user_id: int) -> bool:
        return self.allowed_user_id is not None and user_id == self.allowed_user_id

    def _call(self, method: str, **params):
        resp = self.http.post(f"{self.base}/{method}", json=params, timeout=70)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
        return data["result"]

    def poll(self, offset: int | None, on_cycle=None):
        """Yield (Message, next_offset). Retries quietly on network errors.

        on_cycle is called every loop, so the caller can tell the loop is alive."""
        while True:
            if on_cycle:
                on_cycle()
            try:
                params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    params["offset"] = offset
                for update in self._call("getUpdates", **params):
                    offset = update["update_id"] + 1
                    item = to_message(update) or to_callback(update)
                    if item:
                        yield item, offset
            except Exception as exc:  # network drop, Telegram hiccup
                log.warning("poll error: %s", exc)
                time.sleep(5)

    def notify(self, chat_id: int, text: str, thread_id: int | None = None) -> None:
        for piece in chunk(text):
            params = {"chat_id": chat_id, "text": piece}
            if thread_id:
                params["message_thread_id"] = thread_id
            self._call("sendMessage", **params)

    def send_card(self, chat_id: int, text: str, buttons: list[tuple[str, str]], thread_id: int | None = None) -> int:
        """A message with inline buttons, one per row. Returns the message id."""
        params = {"chat_id": chat_id, "text": text,
                  "reply_markup": {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in buttons]}}
        if thread_id:
            params["message_thread_id"] = thread_id
        return self._call("sendMessage", **params)["message_id"]

    def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self._call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)  # also drops the buttons
        except Exception:
            pass

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        try:
            self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except Exception:
            pass

    def typing(self, chat_id: int, thread_id: int | None = None) -> None:
        params = {"chat_id": chat_id, "action": "typing"}
        if thread_id:
            params["message_thread_id"] = thread_id
        try:
            self._call("sendChatAction", **params)
        except Exception:
            pass
