"""Approval cards: show a run's permission request on my phone, and turn my tap into an answer."""
import json
import logging
import threading
import time

from .approvals import Approvals
from .permissions import Permissions

log = logging.getLogger(__name__)
CODES = {"o": "allow_once", "a": "allow_always", "d": "deny"}


def describe(item: dict) -> str:
    ctx, tool, data = item["ctx"], item["tool_name"], item["input"]
    where = ctx.get("workspace") or "no workspace"
    if ctx.get("task_id"):
        where += f" · task #{ctx['task_id']}"
    if tool == "Bash":
        what = str(data.get("command", ""))[:400]
    elif tool in ("Edit", "Write", "NotebookEdit", "MultiEdit"):
        what = str(data.get("file_path") or data.get("notebook_path") or "")
    else:
        what = json.dumps(data)[:400]
    return f"🔐 Permission needed\n{where}\n\n{tool}: {what}"


class ApprovalCards:
    def __init__(self, cfg, approvals: Approvals, channel, perms: Permissions):
        self.cfg, self.approvals, self.channel, self.perms = cfg, approvals, channel, perms

    def pump(self) -> None:
        """Send a card for every new request."""
        for item in self.approvals.unsent():
            target = item["ctx"].get("notify") or {}
            chat_id = target.get("chat_id") or self.cfg.allowed_user_id
            if chat_id is None:
                continue
            ask_always = self.perms.rule_for(item["ctx"], item["tool_name"], item["input"]) is not None
            buttons = [("Allow once", f"ap:{item['id']}:o")]
            if ask_always:
                buttons.append((f"Always allow for {item['ctx'].get('workspace')}", f"ap:{item['id']}:a"))
            buttons.append(("Deny", f"ap:{item['id']}:d"))
            try:
                message_id = self.channel.send_card(chat_id, describe(item), buttons, target.get("thread_id"))
            except Exception:
                log.exception("could not send approval card")
                continue
            self.approvals.mark_sent(item["id"], {"chat_id": chat_id, "message_id": message_id})

    def handle_callback(self, cb) -> None:
        if not self.channel.is_allowed(cb.user_id):
            self.channel.answer_callback(cb.id, "Not allowed.")
            return
        parts = cb.data.split(":")
        if len(parts) != 3 or parts[0] != "ap" or parts[2] not in CODES:
            self.channel.answer_callback(cb.id, "Unknown button.")
            return
        rid, decision = parts[1], CODES[parts[2]]
        item = self.approvals.get(rid)
        if item is None:
            self.channel.edit_text(cb.chat_id, cb.message_id, "⌛ This request has expired.")
            self.channel.answer_callback(cb.id, "Expired.")
            return
        rule = None
        if decision == "allow_always":
            rule = self.perms.rule_for(item["ctx"], item["tool_name"], item["input"])
            if rule is None:
                decision = "allow_once"        # risky things are never "always"
        if not self.approvals.decide(rid, decision):
            self.channel.edit_text(cb.chat_id, cb.message_id, describe(item) + "\n\n⌛ Already answered or expired.")
            self.channel.answer_callback(cb.id, "Already answered.")
            return
        if rule:
            self.perms.apply(item["ctx"], rule)
        outcome = {"allow_once": "✅ Allowed once", "deny": "❌ Denied",
                   "allow_always": f"✅ Always allowed ({rule['value'] if rule else ''})"}[decision]
        stale = time.time() - item["created"] > self.cfg.approval_wait + 30
        if stale and decision != "deny":
            outcome += "\n(The run had already given up waiting, so ask the agent to try again.)"
        self.channel.edit_text(cb.chat_id, cb.message_id, describe(item) + "\n\n" + outcome)
        self.channel.answer_callback(cb.id, outcome.split("\n")[0])

    def start(self, interval: float = 1.0) -> threading.Thread:
        def loop():
            last_cleanup = 0.0
            while True:
                try:
                    self.pump()
                    if time.time() - last_cleanup > 3600:
                        self.approvals.cleanup()
                        last_cleanup = time.time()
                except Exception:
                    log.exception("approval pump failed")
                time.sleep(interval)

        thread = threading.Thread(target=loop, name="approval-cards", daemon=True)
        thread.start()
        return thread
