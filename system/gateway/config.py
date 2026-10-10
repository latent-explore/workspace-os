"""Settings from the secrets file and the environment."""
import os
from dataclasses import dataclass
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "workspace-os" / "env"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_user_id: int | None
    chat_dir: Path
    state_dir: Path
    model: str | None
    max_runs: int
    claude_bin: str
    run_timeout: int
    task_timeout: int
    schedules_path: Path


def load(env_file: Path = ENV_FILE) -> Config:
    env = {**_read_env_file(env_file), **os.environ}
    allowed = env.get("TELEGRAM_ALLOWED_USER_ID", "").strip()
    state_dir = Path(
        env.get("WOS_STATE_DIR", str(Path.home() / ".local/state/workspace-os"))
    ).expanduser()
    return Config(
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        allowed_user_id=int(allowed) if allowed else None,
        chat_dir=Path(env.get("WOS_CHAT_DIR", str(state_dir / "chat"))).expanduser(),
        state_dir=state_dir,
        model=env.get("WOS_MODEL") or None,
        max_runs=int(env.get("WOS_MAX_RUNS", "1")),
        claude_bin=env.get("WOS_CLAUDE_BIN", "claude"),
        run_timeout=int(env.get("WOS_RUN_TIMEOUT", "600")),
        task_timeout=int(env.get("WOS_TASK_TIMEOUT", "1800")),
        schedules_path=Path(env.get("WOS_SCHEDULES", str(state_dir / "schedules.yaml"))).expanduser(),
    )
