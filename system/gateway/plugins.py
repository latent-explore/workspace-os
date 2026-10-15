"""Plugins: folders with agents and skills. The built-in `wos` plugin is always loaded; installed ones add more."""
import re
from pathlib import Path

import yaml

from .registry import RegistryError, _locked
from .runner import BUILTIN_PLUGIN
from .state import atomic_write
from .workspace import git_clone, parse_repo

NAME = re.compile(r"[a-z][a-z0-9_-]{0,30}")


def frontmatter_name(path: Path) -> str | None:
    """The `name:` in an agent file's frontmatter, else the file name."""
    try:
        text = path.read_text()
    except OSError:
        return None
    if text.startswith("---"):
        for line in text.split("---", 2)[1].splitlines():
            if line.strip().startswith("name:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return path.stem


def plugin_name(plugin_dir: Path) -> str | None:
    import json
    try:
        return json.loads((plugin_dir / ".claude-plugin" / "plugin.json").read_text())["name"]
    except (OSError, KeyError, ValueError):
        return None


def plugin_agents(plugin_dir: Path) -> list[str]:
    """Agent names as Claude Code spells them: plugin:agent."""
    name = plugin_name(plugin_dir)
    if not name:
        return []
    return sorted(f"{name}:{frontmatter_name(p)}" for p in (plugin_dir / "agents").glob("*.md"))


def workspace_agents(workspace_path: Path | None) -> list[str]:
    if not workspace_path:
        return []
    return sorted(frontmatter_name(p) for p in (Path(workspace_path) / ".claude" / "agents").glob("*.md"))


class Plugins:
    """state/plugins.yaml lists the installed plugins (name and folder)."""

    def __init__(self, state_dir: Path, plugins_dir: Path | None = None, builtin: Path = BUILTIN_PLUGIN):
        self.path = state_dir / "plugins.yaml"
        self.plugins_dir = plugins_dir or Path.home() / "plugins"
        self.builtin = builtin

    def _read(self) -> list[dict]:
        try:
            return (yaml.safe_load(self.path.read_text()) or {}).get("plugins") or []
        except FileNotFoundError:
            return []

    def _write(self, items: list[dict]) -> None:
        atomic_write(self.path, yaml.safe_dump({"plugins": items}, sort_keys=False))

    def installed(self) -> list[dict]:
        return [p for p in self._read() if Path(p["path"]).is_dir()]

    def dirs(self) -> list[Path]:
        return [self.builtin, *[Path(p["path"]) for p in self.installed()]]

    def agents(self) -> list[str]:
        return [a for d in self.dirs() for a in plugin_agents(d)]

    def listing(self) -> list[str]:
        lines = [f"wos (built-in): {', '.join(plugin_agents(self.builtin)) or 'no agents'}"]
        for item in self.installed():
            lines.append(f"{item['name']}: {', '.join(plugin_agents(Path(item['path']))) or 'no agents'}")
        return lines

    def add(self, name: str, path: Path) -> None:
        path = Path(path).expanduser().resolve()
        if not NAME.fullmatch(name) or name == "wos":
            raise RegistryError("plugin name: lowercase letters, digits, - or _, and not 'wos'")
        declared = plugin_name(path)
        if declared is None:
            raise RegistryError(f"{path} is not a plugin (no .claude-plugin/plugin.json with a name)")
        taken = {"wos", *[plugin_name(Path(p["path"])) for p in self._read()]}
        if declared in taken:
            raise RegistryError(f"the plugin calls itself {declared!r}, which is already in use")
        with _locked(self.path):
            items = self._read()
            if any(p["name"] == name for p in items):
                raise RegistryError(f"plugin {name!r} is already installed")
            self._write([*items, {"name": name, "path": str(path)}])

    def remove(self, name: str) -> None:
        with _locked(self.path):
            items = self._read()
            if not any(p["name"] == name for p in items):
                raise RegistryError(f"no installed plugin named {name!r}")
            self._write([p for p in items if p["name"] != name])

    def install(self, url: str, name: str | None = None, clone=git_clone, parse=parse_repo) -> str:
        """Clone a GitHub repo into the plugins folder and register it."""
        repo_name, url = parse(url)
        name = name or repo_name.lower().replace(".", "-")
        dest = self.plugins_dir / name
        if dest.exists():
            raise RegistryError(f"{dest} already exists")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        clone(url, dest)
        try:
            self.add(name, dest)
        except Exception:
            import shutil
            shutil.rmtree(dest, ignore_errors=True)       # we created it, so we may remove it
            raise
        return name


def available_agents(plugins: "Plugins", workspace_path: Path | None) -> list[str]:
    """Every agent a workspace can use: plugin agents, and the workspace's own."""
    return plugins.agents() + workspace_agents(workspace_path)
