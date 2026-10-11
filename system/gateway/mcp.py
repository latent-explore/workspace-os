"""MCP servers per workspace, plus the config file handed to each run."""
import json
import re
import sys
import uuid
from pathlib import Path

from .state import atomic_write

NAME = re.compile(r"[a-z][a-z0-9_-]{0,30}")
SYSTEM_DIR = Path(__file__).resolve().parents[1]


class McpError(ValueError):
    pass


class WorkspaceMcp:
    """state/mcp/<workspace>.json: servers the workspace's agents may use. Edited only through bin/wos."""

    def __init__(self, state_dir: Path):
        self.dir = state_dir / "mcp"

    def servers(self, workspace: str | None) -> dict:
        if not workspace:
            return {}
        try:
            return json.loads((self.dir / f"{workspace}.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def add(self, workspace: str, name: str, command: str, args: list[str]) -> None:
        if not NAME.fullmatch(name) or name == "wos":
            raise McpError("MCP server name: lowercase letters, digits, - or _, and not 'wos'")
        servers = self.servers(workspace)
        servers[name] = {"command": command, "args": args}
        atomic_write(self.dir / f"{workspace}.json", json.dumps(servers, indent=2))

    def remove(self, workspace: str, name: str) -> None:
        servers = self.servers(workspace)
        if servers.pop(name, None) is None:
            raise McpError(f"no MCP server {name!r} in {workspace}")
        atomic_write(self.dir / f"{workspace}.json", json.dumps(servers, indent=2))


def write_run_config(state_dir: Path, ctx: dict, workspace_servers: dict, wait_seconds: int = 300) -> Path:
    """The MCP config for one run: our approver plus the workspace's own servers."""
    env = {"WOS_STATE_DIR": str(state_dir), "WOS_CTX": json.dumps(ctx), "WOS_APPROVAL_WAIT": str(wait_seconds),
           "PYTHONPATH": str(SYSTEM_DIR)}
    for key in ("WOS_REGISTRY", "HOME"):
        import os
        if os.environ.get(key):
            env[key] = os.environ[key]
    servers = {**workspace_servers, "wos": {"command": sys.executable, "args": ["-m", "gateway.approver"], "env": env}}
    path = state_dir / "runs" / "mcp" / f"{uuid.uuid4().hex[:12]}.json"
    atomic_write(path, json.dumps({"mcpServers": servers}))
    return path
