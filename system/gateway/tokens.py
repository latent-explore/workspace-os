"""Per-run admin tokens. Only the runner issues one, only for an admin run, and it dies with the run.

bin/wos accepts owner-level changes (permissions, plugins, a workspace's agent) from an agent run only if WOS_ADMIN holds a
live token. A plain "WOS_ADMIN=1" set by some other agent is worthless.
"""
import secrets
import time
from pathlib import Path

MAX_AGE = 6 * 3600


def _dir(state_dir: Path) -> Path:
    return state_dir / "run-tokens"


def issue(state_dir: Path) -> str:
    token = secrets.token_hex(16)
    folder = _dir(state_dir)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / token).write_text(str(time.time()))
    return token


def valid(state_dir: Path, token: str | None) -> bool:
    if not token or not token.isalnum():
        return False
    try:
        issued = float((_dir(state_dir) / token).read_text())
    except (OSError, ValueError):
        return False
    return time.time() - issued < MAX_AGE


def revoke(state_dir: Path, token: str) -> None:
    (_dir(state_dir) / token).unlink(missing_ok=True)


def cleanup(state_dir: Path) -> None:
    for path in _dir(state_dir).glob("*"):
        if time.time() - path.stat().st_mtime > MAX_AGE:
            path.unlink(missing_ok=True)
