"""Git and file helpers the team tools share."""

import os
import re
import subprocess
from pathlib import Path

BRIEF = Path("plan/brief.md")


class ToolError(Exception):
    pass


def git(*args, check=True):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ToolError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def root():
    return Path(git("rev-parse", "--show-toplevel").strip())


def team_dir(repo):
    """.team/ in the worktree, excluded from git once."""
    directory = repo / ".team"
    (directory / "logs").mkdir(parents=True, exist_ok=True)
    exclude = Path(git("rev-parse", "--path-format=absolute", "--git-path", "info/exclude").strip())
    lines = exclude.read_text().splitlines() if exclude.exists() else []
    if ".team/" not in lines:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a") as handle:
            handle.write(".team/\n")
    return directory


def base(repo):
    """The commit the branch started from: TEAM_BASE, .team/base, else the
    merge-base with the default branch (saved, so later commits on main don't
    move it)."""
    if os.environ.get("TEAM_BASE"):
        return git("rev-parse", os.environ["TEAM_BASE"]).strip()
    saved = repo / ".team" / "base"
    if saved.exists():
        return saved.read_text().strip()
    for candidate in ("origin/HEAD", "main", "master"):
        found = git("merge-base", "HEAD", candidate, check=False).strip()
        if found:
            team_dir(repo)
            saved.write_text(found + "\n")
            return found
    raise ToolError("no base: set TEAM_BASE to the commit the branch started from")


def changed_files(repo, since):
    """Files changed since `since`: committed, staged, unstaged and untracked."""
    names = set(git("diff", "--name-only", since).split())
    names |= set(git("ls-files", "--others", "--exclude-standard").split())
    return sorted(n for n in names if not n.startswith(".team/") and "__pycache__/" not in n)


def glob_regex(pattern):
    """A path glob as a regex: ** spans directories, * and ? stay inside one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def matches(path, patterns):
    path = path.removeprefix("./")
    for pattern in patterns:
        pattern = pattern.strip().removeprefix("./")
        if not pattern:
            continue
        if pattern.endswith("/"):
            pattern += "**"
        if glob_regex(pattern).match(path) or (not any(c in pattern for c in "*?") and path.startswith(pattern.rstrip("/") + "/")):
            return True
    return False


def case_id(case):
    return f"{case['file']} > {case['name']}"
