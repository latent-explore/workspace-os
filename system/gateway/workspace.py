"""Create a workspace: a folder with its repos cloned, a template, a local git repo and a registry entry."""
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from .config import REPO_ROOT
from .registry import Registry, RegistryError
from .worktrees import SAFE

NAME = re.compile(r"[a-z][a-z0-9-]{1,30}")
URL = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?")


def parse_repo(url: str) -> tuple[str, str]:
    """Only plain GitHub https URLs are accepted. Returns (folder name, url)."""
    m = URL.fullmatch(url.strip())
    if not m or not SAFE.fullmatch(m.group(2)):
        raise RegistryError(f"not a GitHub https URL: {url!r}")
    return m.group(2), url.strip()


def git_clone(url: str, dest: Path) -> None:
    proc = subprocess.run(
        ["git", "clone", "--quiet", url, str(dest)], capture_output=True, text=True,
        timeout=600, env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/usr/local/bin", "HOME": str(Path.home())},
    )
    if proc.returncode != 0:
        raise RegistryError(f"clone failed for {url}: {proc.stderr.strip()[:200]}")


def _copy_template(template: Path, dest: Path, name: str) -> None:
    for src in sorted(template.rglob("*")):
        rel = src.relative_to(template)
        if src.is_dir():
            (dest / rel).mkdir(parents=True, exist_ok=True)
        elif src.name.endswith(".tmpl"):
            target = dest / rel.with_name(src.name[: -len(".tmpl")])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(src.read_text().replace("{name}", name))
        else:
            (dest / rel.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / rel)


def create_workspace(name: str, repo_urls: list[str], workspaces_dir: Path, registry: Registry,
                     template: Path = REPO_ROOT / "base" / "template",
                     parse=parse_repo, clone=git_clone) -> dict:
    if not NAME.fullmatch(name):
        raise RegistryError("workspace name: lowercase letters, digits and dashes, starting with a letter")
    path = (workspaces_dir / name).resolve()
    if name in registry.all() or path.exists():
        raise RegistryError(f"workspace {name!r} already exists")
    repos = [parse(u) for u in repo_urls]
    names = [r[0] for r in repos]
    if len(set(names)) != len(names):
        raise RegistryError("two repos would use the same folder name")
    path.mkdir(parents=True)  # we created it, so we may remove it if anything fails
    try:
        if template.exists():
            _copy_template(template, path, name)
        for repo_name, url in repos:
            clone(url, path / repo_name)
        (path / "repos.yaml").write_text(yaml.safe_dump({"repos": dict(repos)}, sort_keys=True))
        ignore = [".worktrees/", *[f"{n}/" for n in names]]
        (path / ".gitignore").write_text("\n".join(ignore) + "\n")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
        subprocess.run(["git", "add", "-A"], cwd=path, check=True)
        committed = subprocess.run(["git", "commit", "-qm", f"Create workspace {name}"], cwd=path,
                                   capture_output=True).returncode == 0
        registry.add(name, path)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return {"name": name, "path": str(path), "repos": names, "committed": committed}


def workspace_repos(path: Path) -> list[str]:
    """Repo folder names listed in a workspace's repos.yaml."""
    try:
        return sorted((yaml.safe_load((Path(path) / "repos.yaml").read_text()) or {}).get("repos") or {})
    except FileNotFoundError:
        return []
