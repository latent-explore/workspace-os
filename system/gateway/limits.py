"""Limits the owner can change at runtime, and the gate that enforces the run cap."""
import threading
from pathlib import Path

import yaml

from .state import atomic_write


class Limits:
    """state/limits.yaml. The run cap can be changed up to a hard maximum, without a restart."""

    def __init__(self, state_dir: Path, default_max_runs: int = 1, hard_max_runs: int = 100):
        self.path = state_dir / "limits.yaml"
        self.hard_max = max(1, hard_max_runs)
        self.default = min(max(1, default_max_runs), self.hard_max)

    def max_runs(self) -> int:
        try:
            value = int((yaml.safe_load(self.path.read_text()) or {}).get("max_runs", self.default))
        except (FileNotFoundError, ValueError, TypeError):
            value = self.default
        return min(max(1, value), self.hard_max)

    def set_max_runs(self, value: int) -> None:
        if not 1 <= value <= self.hard_max:
            raise ValueError(f"max_runs must be between 1 and {self.hard_max}")
        atomic_write(self.path, yaml.safe_dump({"max_runs": value}))


class RunGate:
    """Lets at most `limits.max_runs()` runs go at once. The cap is read fresh, so changes apply at once."""

    def __init__(self, limits: Limits):
        self.limits = limits
        self._active = 0
        self._cond = threading.Condition()

    @property
    def active(self) -> int:
        return self._active

    def __enter__(self):
        with self._cond:
            while self._active >= self.limits.max_runs():
                self._cond.wait(1.0)
            self._active += 1
        return self

    def __exit__(self, *exc):
        with self._cond:
            self._active -= 1
            self._cond.notify_all()
