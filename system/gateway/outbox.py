"""Outbox: agents and the CLI never touch the bot token. They drop a message here; the gateway sends it."""
import json
import time
from pathlib import Path

from .state import atomic_write


def enqueue(state_dir: Path, text: str, target: dict | None = None) -> None:
    """target = {"chat_id": ..., "thread_id": ...}; None means my private chat."""
    path = state_dir / "outbox" / f"{time.time_ns()}.json"
    atomic_write(path, json.dumps({"target": target, "text": text}))


def drain(state_dir: Path, send) -> int:
    """Send queued messages in order. Stops at the first failure so nothing is lost or reordered."""
    sent = 0
    for path in sorted((state_dir / "outbox").glob("*.json")):
        try:
            item = json.loads(path.read_text())
            send(item.get("target"), item["text"])
        except Exception:
            break
        path.unlink(missing_ok=True)
        sent += 1
    return sent
