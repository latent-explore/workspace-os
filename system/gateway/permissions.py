"""Per-workspace permissions. One place decides: allow, ask (raise a card) or deny.

Stored in state/permissions.yaml, outside every workspace, so an agent cannot grant itself anything.
This is a policy layer for agents acting in good faith and for prompt-injection resistance.
The hard wall stays the wos Linux user and the systemd sandbox.
"""
import fcntl
import json
import re
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import yaml

from .registry import Registry
from .state import atomic_write

CAPABILITIES = {
    "edit_files": "edit and create files inside the workspace",
    "shell": "run shell commands (risky ones still ask)",
    "web": "fetch web pages and search the web",
    "github": "use GitHub through `bin/wos gh`, on the workspace's repos only",
    "create_repos": "create private GitHub repos for the workspace",
    "mcp_manage": "add or remove MCP servers for the workspace",
}
DEFAULT_CAPS = ("edit_files", "shell")
SECRET_HINT = ".config/workspace-os"
# Text that must never appear in an ordinary agent's tool call: the secrets folder, the rule files and the
# admin proof. Admin gets a card instead of a refusal.
PROTECTED_TEXT = (SECRET_HINT, "run-tokens", "WOS_ADMIN", "WOS_AGENT", "permissions.yaml", "plugins.yaml")
# A saved Bash rule matches by prefix, so a command that can run or write something else is never covered by one.
UNSAFE_SHELL = ("$(", "${", "`", ">", "<")
EDIT_TOOLS = ("Edit", "Write", "NotebookEdit", "MultiEdit")
# Always ask, even with the shell capability, and never offer "always allow".
RISKY = [re.compile(p) for p in (
    r"(^|[\s;&|(`])(sudo|su|doas)\s", r"\brm\s+-\w*[rf]\w*[rf]", r"\bgit\s+push\b", r"\bgit\s+reset\s+--hard",
    r"\bgit\s+clean\b", r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b", r"(^|[\s;&|(`])(chmod|chown|dd|mkfs|shutdown|reboot|"
    r"systemctl|loginctl|userdel|useradd|passwd|kill|pkill)\s", r">\s*/dev/",
)]
RAW_GH = re.compile(r"(?<!wos )(?<![\w/.-])gh\s")           # raw gh bypasses the per-workspace GitHub rules
# These take a subcommand that changes what they do, so a rule keeps it: "npm install", not just "npm".
SUBCOMMAND_TOOLS = {"git", "npm", "npx", "yarn", "pnpm", "pip", "pip3", "cargo", "go", "docker", "kubectl", "apt", "dnf", "make"}
INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "perl", "ruby", "env", "eval", "xargs", "exec",
                # these can run or overwrite arbitrary things, so they are never "always allow"
                "find", "sed", "tar", "awk", "rsync", "ssh", "scp", "nohup", "timeout", "nice", "git-shell"}
SEGMENT = re.compile(r"\|\||&&|;|\||\n")


@dataclass
class Decision:
    kind: str       # allow | ask | deny
    reason: str = ""


def touches_protected(tool_input: dict) -> bool:
    text = json.dumps(tool_input)
    return any(hint in text for hint in PROTECTED_TEXT)


def segments(command: str) -> list[str]:
    parts = [p.strip() for p in SEGMENT.split(command)]
    cleaned = []
    for part in parts:
        part = re.sub(r"^(\w+=\S+\s+)+", "", part)          # leading VAR=value
        if part and not part.startswith("cd "):
            cleaned.append(part)
    return cleaned


def wos_words(segment: str) -> list[str]:
    """'bin/wos plugin install X' -> ['plugin', 'install', 'X']. Also ./bin/wos, a path ending in bin/wos, or $WOS_BIN."""
    words = segment.split()
    if words and (words[0].endswith("bin/wos") or words[0] in ("$WOS_BIN", '"$WOS_BIN"')) and len(words) > 1:
        return words[1:]
    return []


def is_wos_command(segment: str) -> str | None:
    words = wos_words(segment)
    return words[0] if words else None


def rule_matches(rule: str, tool_name: str, tool_input: dict) -> bool:
    """Rules look like 'Write', 'mcp__x__*' or 'Bash(npm install:*)'."""
    m = re.fullmatch(r"(\w+)\((.*):\*\)", rule)
    if m and m.group(1) == "Bash" and tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        if any(t in command for t in UNSAFE_SHELL):
            return False
        segs = segments(command)
        return bool(segs) and all(s.startswith(m.group(2)) for s in segs)
    return fnmatch(tool_name, rule)


class Permissions:
    def __init__(self, state_dir: Path, registry: Registry):
        self.path = state_dir / "permissions.yaml"
        self.registry = registry

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_name(self.path.name + ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            return (yaml.safe_load(self.path.read_text()) or {}).get("workspaces") or {}
        except FileNotFoundError:
            return {}

    def _edit(self, workspace: str, change) -> dict:
        with self._locked():
            data = self._read()
            entry = data.setdefault(workspace, {})
            entry.setdefault("capabilities", [])
            entry.setdefault("allow", [])
            change(entry)
            atomic_write(self.path, yaml.safe_dump({"workspaces": data}, sort_keys=True))
            return entry

    def show(self, workspace: str) -> dict:
        entry = self._read().get(workspace, {})
        return {"capabilities": entry.get("capabilities", []), "allow": entry.get("allow", []),
                "github": entry.get("github", {})}

    def caps(self, workspace: str | None) -> set[str]:
        return set(self.show(workspace)["capabilities"]) if workspace else set()

    def seed(self, workspace: str) -> None:
        """New workspaces get the default capabilities. Existing settings are never overwritten."""
        if workspace not in self._read():
            self._edit(workspace, lambda e: e.update(capabilities=list(DEFAULT_CAPS)))

    def grant(self, workspace: str, capability: str) -> None:
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown capability {capability!r}. Choose from: {', '.join(CAPABILITIES)}")
        self._edit(workspace, lambda e: e["capabilities"].append(capability) if capability not in e["capabilities"] else None)

    def revoke(self, workspace: str, capability: str) -> None:
        self._edit(workspace, lambda e: e.update(capabilities=[c for c in e["capabilities"] if c != capability]))

    def add_rule(self, workspace: str, rule: str) -> None:
        self._edit(workspace, lambda e: e["allow"].append(rule) if rule not in e["allow"] else None)

    def remove_rule(self, workspace: str, rule: str) -> None:
        self._edit(workspace, lambda e: e.update(allow=[r for r in e["allow"] if r != rule]))

    # ---------- GitHub scope ----------
    def github_patterns(self, workspace: str) -> list[str]:
        """Repos this workspace's agents may touch: its own repos, its ws-<name> repo, and anything listed."""
        patterns = list(self.show(workspace)["github"].get("repos", []))
        entry = self.registry.all().get(workspace)
        owner = None
        if entry:
            try:
                repos = (yaml.safe_load((Path(entry["path"]) / "repos.yaml").read_text()) or {}).get("repos") or {}
            except FileNotFoundError:
                repos = {}
            for url in repos.values():
                m = re.match(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", url)
                if m:
                    owner = owner or m.group(1)
                    patterns.append(f"{m.group(1)}/{m.group(2)}")
        if owner:
            patterns += [f"{owner}/ws-{workspace}", f"{owner}/{workspace}-*"]
        return patterns

    def github_api_allowed(self, workspace: str) -> bool:
        return bool(self.show(workspace)["github"].get("api"))

    # ---------- the decision ----------
    def workspace_dir(self, workspace: str | None) -> Path | None:
        entry = self.registry.all().get(workspace) if workspace else None
        return Path(entry["path"]).resolve() if entry else None

    def workspace_of_path(self, path: Path) -> str | None:
        for name, entry in self.registry.all().items():
            if path.is_relative_to(Path(entry["path"]).resolve()):
                return name
        return None

    @staticmethod
    def scope(ctx: dict) -> str | None:
        """Whose grants apply: the workspace, or "_admin" for the admin agent (which has no workspace)."""
        return ctx.get("workspace") or ("_admin" if ctx.get("mode") == "admin" else None)

    def check(self, ctx: dict, tool_name: str, tool_input: dict) -> Decision:
        """allow / ask / deny. For admin, what is a hard deny for other agents becomes an ask."""
        ws, scope = ctx.get("workspace"), self.scope(ctx)
        admin = scope == "_admin"
        if touches_protected(tool_input):
            return Decision("ask", "the secrets or permission files") if admin else Decision("deny", "The secrets and permission files are off limits.")
        if tool_name in ("Read", "Glob", "Grep"):
            return Decision("allow")
        rules = self.show(scope)["allow"] if scope else []
        if tool_name == "Bash":
            return self.check_bash(ctx, str(tool_input.get("command", "")), rules)
        if tool_name in EDIT_TOOLS:
            raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            path = Path(raw).expanduser().resolve()
            if path.is_relative_to(self.path.parent.resolve()):        # the system's own state: rules, tasks, tokens
                return Decision("ask", "system state") if admin else Decision("deny", "The system's state files are off limits.")
            target = ws or self.workspace_of_path(path)
            root = self.workspace_dir(target)
            if target and root and path.is_relative_to(root) and "edit_files" in self.caps(target):
                return Decision("allow")
            if any(rule_matches(r, tool_name, tool_input) for r in rules):
                return Decision("allow")
            return Decision("ask", f"edit outside granted scope: {path}")
        if tool_name in ("WebFetch", "WebSearch") and "web" in self.caps(ws):
            return Decision("allow")
        if any(rule_matches(r, tool_name, tool_input) for r in rules):
            return Decision("allow")
        return Decision("ask")

    def check_bash(self, ctx: dict, command: str, rules: list[str]) -> Decision:
        ws, scope = ctx.get("workspace"), self.scope(ctx)
        admin = scope == "_admin"
        owner_only = lambda why: Decision("ask", why) if admin else Decision("deny", f"{why}: only the owner can do this.")
        if RAW_GH.search(command):
            if admin:
                return Decision("ask", "raw gh")
            return Decision("deny", "Use `bin/wos gh --workspace NAME -- ...` instead of raw gh; it limits GitHub access per workspace.")
        segs = segments(command)
        if not segs:
            return Decision("allow")
        for seg in segs:
            words = wos_words(seg)
            sub = words[0] if words else None
            if sub == "admin":
                return Decision("deny", "The admin agent is started by the owner in a terminal.")
            if not admin and (sub in ("bind", "unbind") or words[:2] == ["workspace", "add"]):
                return Decision("deny", "Only the admin agent creates workspaces or links groups.")
            if sub == "perms" or (sub == "plugin" and words[1:2] != ["list"]) \
                    or words[:2] == ["workspace", "agent"]:
                return owner_only("changing permissions, plugins or a workspace's agent")
            if sub in ("gh", "mcp"):
                target = ws or (re.search(r"--workspace[ =](\S+)", seg) or [None, None])[1]
                need = "github" if sub == "gh" else "mcp_manage"
                if need not in self.caps(target):
                    return Decision("ask", f"{need} for {target}")
        if any(p.search(command) for p in RISKY):
            return Decision("ask", "risky command")
        if all(is_wos_command(s) for s in segs):
            return Decision("allow")
        if "shell" in self.caps(ws) or rule_covers_all(rules, segs):
            return Decision("allow")
        return Decision("ask")

    def rule_for(self, ctx: dict, tool_name: str, tool_input: dict) -> dict | None:
        """What 'Always allow' would add. None means: only once, never always."""
        scope = self.scope(ctx)
        if not scope or touches_protected(tool_input):
            return None
        admin = scope == "_admin"
        if tool_name in EDIT_TOOLS or tool_name in ("WebFetch", "WebSearch"):
            raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
            if raw and Path(raw).expanduser().resolve().is_relative_to(self.path.parent.resolve()):
                return None                        # never "always" for the system's own state files
            if admin:
                return {"kind": "rule", "value": tool_name}
            return {"kind": "cap", "value": "edit_files" if tool_name in EDIT_TOOLS else "web"}
        if tool_name.startswith("mcp__"):
            server = tool_name.split("__")[1] if tool_name.count("__") >= 2 else tool_name
            return {"kind": "rule", "value": f"mcp__{server}__*"}      # the whole server (a connector, say Gmail)
        if tool_name == "Bash":
            command = str(tool_input.get("command", ""))
            if any(p.search(command) for p in RISKY) or RAW_GH.search(command) or any(t in command for t in UNSAFE_SHELL):
                return None
            segs = segments(command)
            if len(segs) != 1 or is_wos_command(segs[0]):
                return None
            words = segs[0].split()
            if not words or words[0] in INTERPRETERS:
                return None
            if words[0] in SUBCOMMAND_TOOLS:
                if len(words) < 2 or words[1].startswith(("-", "/", ".")):
                    return None                    # "git -c alias.x=..." and the like: too open for a prefix rule
                prefix = " ".join(words[:2])
            else:
                prefix = words[0]
            return {"kind": "rule", "value": f"Bash({prefix}:*)"}
        return {"kind": "rule", "value": tool_name}

    def apply(self, ctx: dict, rule: dict) -> None:
        scope = self.scope(ctx)
        (self.grant if rule["kind"] == "cap" else self.add_rule)(scope, rule["value"])


def rule_covers_all(rules: list[str], segs: list[str]) -> bool:
    return all(any(rule_matches(r, "Bash", {"command": s}) for r in rules) for s in segs)
