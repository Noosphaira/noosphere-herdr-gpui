"""Folder grants for sandboxed agents, from ~/.config/herdr-launch.

global.yaml applies to every repo; repos.yaml adds per-repo entries:

    # global.yaml
    read_only: [~/.gitconfig]
    read_write: []
    env: []      # extra environment variable names passed into the sandbox
    path: []     # extra PATH directories (they must also be granted)

    # repos.yaml
    - repo: ~/code/myapp
      read_write: [~/design-assets/myapp]
      read_only: [~/code/shared-ui]
"""

import os
from pathlib import Path

import yaml

HOME = Path.home()
CONFIG_DIR = HOME / ".config/herdr-launch"

# No grant may equal, contain, or sit inside these.
FORBIDDEN = [
    HOME / ".ssh",
    HOME / ".gnupg",
    CONFIG_DIR,
    HOME / ".config/herdr",
]
# Read-write grants additionally may not touch these: an agent that could
# write them could widen its next sandbox or run code outside it.
FORBIDDEN_WRITE = [
    HOME / ".config/opencode",
    HOME / ".local/bin",
    HOME / ".local/share/herdr-launch",
    HOME / ".config/systemd",
    HOME / ".config/autostart",
]
# Walking a read-write grant for nested .git/hooks stops after this many entries.
WALK_LIMIT = 200_000


class GrantError(Exception):
    pass


def expand(raw):
    return Path(os.path.expanduser(str(raw))).resolve()


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def read_yaml(path, default):
    if not path.exists():
        return default
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise GrantError(f"{path}: {error}") from error
    return default if data is None else data


def string_list(value, where):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GrantError(f"{where} must be a list of strings")
    return value


def contains_git_hooks(root):
    seen = 0
    for current, dirs, _ in os.walk(root):
        if ".git" in dirs or Path(current).name == ".git":
            return True
        seen += len(dirs)
        if seen > WALK_LIMIT:
            raise GrantError(f"{root} is too large to check for .git/hooks; grant a narrower folder")
    return False


def check(path, mode, where):
    if not path.exists():
        raise GrantError(f"{where}: {path} does not exist")
    if path == HOME or path in HOME.parents:
        raise GrantError(f"{where}: {path} covers the home directory")
    for forbidden in FORBIDDEN:
        if overlaps(path, forbidden):
            raise GrantError(f"{where}: {path} overlaps {forbidden}")
    if mode == "read_write":
        for forbidden in FORBIDDEN_WRITE:
            if overlaps(path, forbidden):
                raise GrantError(f"{where}: {path} overlaps {forbidden}, which must stay read-only")
        if path.name == "hooks" and path.parent.name == ".git" or (path.is_dir() and contains_git_hooks(path)):
            raise GrantError(f"{where}: {path} contains a git repository, whose hooks must stay read-only")


def load(repo):
    """Return {'read_only', 'read_write', 'env', 'path'} for a main checkout."""
    repo = Path(repo).resolve()
    global_cfg = read_yaml(CONFIG_DIR / "global.yaml", {})
    repos_cfg = read_yaml(CONFIG_DIR / "repos.yaml", [])
    if not isinstance(global_cfg, dict):
        raise GrantError("global.yaml must be a mapping")
    if not isinstance(repos_cfg, list):
        raise GrantError("repos.yaml must be a list")

    sections = [("global.yaml", global_cfg)]
    for index, entry in enumerate(repos_cfg):
        if not isinstance(entry, dict) or "repo" not in entry:
            raise GrantError(f"repos.yaml entry {index} needs a repo key")
        if expand(entry["repo"]) == repo:
            sections.append((f"repos.yaml ({entry['repo']})", entry))

    grants = {"read_only": [], "read_write": [], "env": [], "path": []}
    for where, section in sections:
        for mode in ("read_only", "read_write"):
            for raw in string_list(section.get(mode), f"{where} {mode}"):
                path = expand(raw)
                check(path, mode, where)
                grants[mode].append(path)
    grants["env"] = string_list(global_cfg.get("env"), "global.yaml env")
    grants["path"] = [str(expand(p)) for p in string_list(global_cfg.get("path"), "global.yaml path")]
    return grants
