"""bin/wos gh: GitHub access limited per workspace. Agents never call raw `gh`.

Checks which repo the command touches against the workspace's allow list, blocks dangerous
subcommands, and forces new repos to be private. Then it runs the real gh.
"""
import re
import subprocess
from fnmatch import fnmatch
from pathlib import Path

from .permissions import Permissions

NEVER = {"auth", "ssh-key", "gpg-key", "secret", "variable", "extension", "config", "alias", "codespace", "gist"}
REPO_NEVER = {"delete", "archive", "unarchive", "rename", "transfer"}


class GhDenied(ValueError):
    pass


def current_repo(cwd: Path) -> str | None:
    out = subprocess.run(["git", "remote", "get-url", "origin"], cwd=cwd, capture_output=True, text=True).stdout.strip()
    m = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?$", out)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def vet(args: list[str], workspace: str, perms: Permissions, cwd: Path) -> list[str]:
    """Return the (possibly adjusted) gh arguments, or raise GhDenied with a reason."""
    caps = perms.caps(workspace)
    if "github" not in caps:
        raise GhDenied(f"workspace {workspace!r} has no GitHub access. The user can grant it with /grant {workspace} github")
    if not args:
        raise GhDenied("no gh command given")
    top = args[0]
    if top in NEVER:
        raise GhDenied(f"`gh {top}` is not allowed")
    if top == "api" and not perms.github_api_allowed(workspace):
        raise GhDenied("`gh api` is not allowed for this workspace")
    sub = args[1] if len(args) > 1 else ""
    if top == "repo" and sub in REPO_NEVER:
        raise GhDenied(f"`gh repo {sub}` is not allowed")
    patterns = perms.github_patterns(workspace)

    def allowed(repo: str) -> bool:
        return any(fnmatch(repo, p) for p in patterns)

    if top == "repo" and sub == "create":
        if "create_repos" not in caps:
            raise GhDenied(f"creating repos is not granted. The user can allow it with /grant {workspace} create_repos")
        names = [a for a in args[2:] if not a.startswith("-")]
        if not names or not allowed(names[0] if "/" in names[0] else f"*/{names[0]}"):
            raise GhDenied(f"repo name must match this workspace's allow list: {', '.join(patterns) or '(none)'}")
        if "--public" in args or "--internal" in args:
            raise GhDenied("new repos must be private")
        return args if "--private" in args else [*args, "--private"]
    explicit = next((args[i + 1] for i, a in enumerate(args[:-1]) if a in ("-R", "--repo")), None)
    explicit = explicit or next((a.split("=", 1)[1] for a in args if a.startswith("--repo=")), None)
    repo = explicit or current_repo(cwd)
    if top != "api" and (repo is None or not allowed(repo)):
        raise GhDenied(f"{repo or 'this folder'} is not in this workspace's allow list: {', '.join(patterns) or '(none)'}")
    return args
