"""Scheduler: one thread that fires due jobs. No tokens are spent until a job runs an agent."""
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import yaml

from .state import atomic_write

log = logging.getLogger(__name__)
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
UNITS = {"m": "minutes", "h": "hours", "d": "days"}


@dataclass(frozen=True)
class Entry:
    id: str
    when: str
    run: dict
    owner: str = "system"
    until: date | None = None


def _clock(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


def next_fire(when: str, last_run: datetime | None) -> datetime | None:
    """First fire time strictly after last_run. Raises ValueError for a bad `when`.

    daily 07:00 | weekly mon 08:30 | every 30m (m/h/d) | once 2026-10-12 09:00
    """
    parts = when.split()
    kind = parts[0] if parts else ""
    if kind == "once" and len(parts) == 3:
        at = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M")
        return at if last_run is None else None
    if last_run is None:
        raise ValueError("last_run needed")
    if kind == "daily" and len(parts) == 2:
        cand = datetime.combine(last_run.date(), _clock(parts[1]))
        return cand if cand > last_run else cand + timedelta(days=1)
    if kind == "weekly" and len(parts) == 3 and parts[1] in WEEKDAYS:
        for i in range(8):
            day = last_run.date() + timedelta(days=i)
            cand = datetime.combine(day, _clock(parts[2]))
            if day.weekday() == WEEKDAYS.index(parts[1]) and cand > last_run:
                return cand
    if kind == "every" and len(parts) == 2:
        m = re.fullmatch(r"(\d+)([mhd])", parts[1])
        if m and int(m.group(1)) > 0:
            return last_run + timedelta(**{UNITS[m.group(2)]: int(m.group(1))})
    raise ValueError(f"bad schedule: {when!r}")


def parse_entries(data) -> list[Entry]:
    entries = []
    for raw in (data or {}).get("schedules", []) or []:
        try:
            until = raw.get("until")
            if isinstance(until, str):
                until = date.fromisoformat(until)
            entry = Entry(str(raw["id"]), str(raw["when"]), dict(raw["run"]), raw.get("owner", "system"), until)
            if not ({"message", "prompt"} & entry.run.keys()):
                raise ValueError("run needs 'message' or 'prompt'")
            next_fire(entry.when, datetime.now())
            entries.append(entry)
        except (KeyError, ValueError, TypeError) as exc:
            log.warning("skipping bad schedule entry %r: %s", raw, exc)
    return entries


class Scheduler:
    def __init__(self, schedules_path: Path, state_dir: Path, fire: Callable[[Entry], None],
                 on_tick: Callable[[], None] | None = None, interval: int = 60):
        self.schedules_path = schedules_path
        self.state_path = state_dir / "schedule_state.json"
        self.fire, self.on_tick, self.interval = fire, on_tick, interval
        self._lock = threading.Lock()
        self._stop = threading.Event()

    # --- state on disk: survives restarts, enables catch-up ---
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"last_run": {}, "paused": []}

    def _save_state(self, state: dict) -> None:
        atomic_write(self.state_path, json.dumps(state, indent=2))

    def entries(self) -> list[Entry]:
        try:
            return parse_entries(yaml.safe_load(self.schedules_path.read_text()))
        except FileNotFoundError:
            return []
        except yaml.YAMLError as exc:
            log.warning("cannot read %s: %s", self.schedules_path, exc)
            return []

    def tick(self, now: datetime) -> list[str]:
        """Fire everything due. Returns the ids fired."""
        fired = []
        with self._lock:
            state = self._load_state()
            for e in self.entries():
                if e.id in state["paused"] or (e.until and now.date() > e.until):
                    continue
                last = state["last_run"].get(e.id)
                last = datetime.fromisoformat(last) if last else None
                if last is None and not e.when.startswith("once"):
                    state["last_run"][e.id] = now.isoformat()  # new entry: start counting from now
                    continue
                due = next_fire(e.when, last)
                if due and due <= now:
                    state["last_run"][e.id] = now.isoformat()
                    self._save_state(state)  # save first: never fire twice after a crash
                    fired.append(e.id)
                    try:
                        self.fire(e)
                    except Exception:
                        log.exception("schedule %s failed to start", e.id)
            self._save_state(state)
        return fired

    def status(self, now: datetime) -> list[str]:
        state = self._load_state()
        lines = []
        for e in self.entries():
            if e.id in state["paused"]:
                note = "paused"
            elif e.until and now.date() > e.until:
                note = "expired"
            else:
                last = state["last_run"].get(e.id)
                nxt = next_fire(e.when, datetime.fromisoformat(last) if last else None) if last or e.when.startswith("once") else None
                note = f"next {nxt:%a %d %b %H:%M}" if nxt else "done" if e.when.startswith("once") else "starting"
            lines.append(f"{e.id}: {e.when} ({note})")
        return lines

    def set_paused(self, entry_id: str, paused: bool) -> bool:
        if entry_id not in {e.id for e in self.entries()}:
            return False
        with self._lock:
            state = self._load_state()
            ids = set(state["paused"])
            ids.add(entry_id) if paused else ids.discard(entry_id)
            state["paused"] = sorted(ids)
            self._save_state(state)
        return True

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick(datetime.now())
            except Exception:
                log.exception("scheduler tick failed")
            if self.on_tick:
                self.on_tick()
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.run_forever, name="scheduler", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
