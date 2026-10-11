"""Approval queue: a run asks, the gateway shows me a card, my tap answers. Plain files, crash-safe."""
import json
import time
import uuid
from pathlib import Path

from .state import atomic_write

DECISIONS = ("allow_once", "allow_always", "deny")


class Approvals:
    def __init__(self, state_dir: Path):
        self.pending = state_dir / "approvals" / "pending"
        self.decided = state_dir / "approvals" / "decided"

    def request(self, ctx: dict, tool_name: str, tool_input: dict) -> str:
        rid = uuid.uuid4().hex[:10]
        atomic_write(self.pending / f"{rid}.json", json.dumps({
            "id": rid, "ctx": ctx, "tool_name": tool_name, "input": tool_input,
            "created": time.time(), "sent": False,
        }))
        return rid

    def get(self, rid: str) -> dict | None:
        try:
            return json.loads((self.pending / f"{rid}.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def unsent(self) -> list[dict]:
        found = []
        for path in sorted(self.pending.glob("*.json")):
            try:
                item = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if not item["sent"]:
                found.append(item)
        return found

    def mark_sent(self, rid: str, message: dict | None = None) -> None:
        item = self.get(rid)
        if item:
            item["sent"], item["message"] = True, message
            atomic_write(self.pending / f"{rid}.json", json.dumps(item))

    def decide(self, rid: str, decision: str, by: str = "me") -> bool:
        """Record my answer. False if the request is unknown or already answered."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        if self.get(rid) is None or (self.decided / f"{rid}.json").exists():
            return False
        atomic_write(self.decided / f"{rid}.json", json.dumps({"decision": decision, "by": by, "at": time.time()}))
        return True

    def answer(self, rid: str) -> dict | None:
        try:
            return json.loads((self.decided / f"{rid}.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def wait(self, rid: str, timeout: float, poll: float = 0.5) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            answer = self.answer(rid)
            if answer:
                return answer
            time.sleep(poll)
        return None

    def cleanup(self, older_than: float = 86400) -> None:
        cutoff = time.time() - older_than
        for folder in (self.pending, self.decided):
            for path in folder.glob("*.json"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
