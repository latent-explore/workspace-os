"""Small on-disk state: session map and run logs. Survives crashes."""
import json
import os
import tempfile
import time
from pathlib import Path


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


class Sessions:
    """Maps a chat topic to a Claude session ID."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "sessions.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def get(self, key: str) -> str | None:
        return self._load().get(key)

    def set(self, key: str, session_id: str) -> None:
        data = self._load()
        data[key] = session_id
        _atomic_write(self.path, json.dumps(data, indent=2))

    def clear(self, key: str) -> None:
        data = self._load()
        if data.pop(key, None) is not None:
            _atomic_write(self.path, json.dumps(data, indent=2))


class Offset:
    """Last Telegram update handled, so a restart does not replay messages."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "telegram_offset"

    def get(self) -> int | None:
        try:
            return int(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return None

    def set(self, value: int) -> None:
        _atomic_write(self.path, str(value))


def write_run_log(state_dir: Path, key: str, mode: str, result) -> Path:
    """One JSON file per run: the base for reports and the improver later."""
    now = time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    name = f"{int(now * 1000)}-{key.replace(':', '_')}.json"
    path = state_dir / "runs" / day / name
    _atomic_write(path, json.dumps({
        "time": now, "key": key, "mode": mode, "status": result.status,
        "session_id": result.session_id, "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens, "cost_usd": result.cost_usd,
        "text": result.text,
    }, indent=2))
    return path
