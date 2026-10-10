"""Registry (workspaces) and chats (Reception + known groups). Small files, safe to edit live."""
import fcntl
import json
from contextlib import contextmanager
from pathlib import Path

import yaml

from .state import atomic_write


class RegistryError(ValueError):
    pass


@contextmanager
def _locked(path: Path):
    """One writer at a time: the gateway and the bin/wos helper both edit these files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Registry:
    """workspaces.yaml: name -> path and the Telegram group it is linked to."""

    def __init__(self, path: Path):
        self.path = path

    def all(self) -> dict[str, dict]:
        try:
            return (yaml.safe_load(self.path.read_text()) or {}).get("workspaces") or {}
        except FileNotFoundError:
            return {}

    def _write(self, workspaces: dict) -> None:
        atomic_write(self.path, yaml.safe_dump({"workspaces": workspaces}, sort_keys=True))

    def add(self, name: str, path: Path) -> None:
        with _locked(self.path):
            workspaces = self.all()
            if name in workspaces:
                raise RegistryError(f"workspace {name!r} already exists")
            workspaces[name] = {"path": str(path)}
            self._write(workspaces)

    def bind(self, name: str, chat_id: int) -> None:
        with _locked(self.path):
            workspaces = self.all()
            if name not in workspaces:
                raise RegistryError(f"no workspace named {name!r}")
            for other, entry in workspaces.items():
                if other != name and entry.get("telegram_chat") == chat_id:
                    raise RegistryError(f"that group is already linked to {other!r}")
            workspaces[name]["telegram_chat"] = chat_id
            self._write(workspaces)

    def unbind(self, name: str) -> None:
        with _locked(self.path):
            workspaces = self.all()
            if name not in workspaces:
                raise RegistryError(f"no workspace named {name!r}")
            workspaces[name].pop("telegram_chat", None)
            self._write(workspaces)

    def by_chat(self, chat_id: int) -> tuple[str, dict] | None:
        for name, entry in self.all().items():
            if entry.get("telegram_chat") == chat_id:
                return name, entry
        return None


class Chats:
    """chats.json: which group is Reception, and every group the bot has heard from."""

    def __init__(self, state_dir: Path):
        self.path = state_dir / "chats.json"

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"admin": None, "known": {}}

    def admin(self) -> int | None:
        return self._load()["admin"]

    def set_admin(self, chat_id: int) -> bool:
        """Reception can be set once. Returns False if another group already is Reception."""
        with _locked(self.path):
            data = self._load()
            if data["admin"] not in (None, chat_id):
                return False
            data["admin"] = chat_id
            atomic_write(self.path, json.dumps(data, indent=2))
            return True

    def see(self, chat_id: int, title: str, kind: str) -> None:
        entry = {"title": title, "type": kind}
        if self._load()["known"].get(str(chat_id)) == entry:
            return  # nothing new: no write
        with _locked(self.path):
            data = self._load()
            data["known"][str(chat_id)] = entry
            atomic_write(self.path, json.dumps(data, indent=2))

    def known(self) -> dict[int, dict]:
        return {int(k): v for k, v in self._load()["known"].items()}
