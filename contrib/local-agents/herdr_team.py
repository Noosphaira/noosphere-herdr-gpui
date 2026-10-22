"""A launched team's shared files, outside every sandbox.

herdr-launch writes `team.json` into a private directory under
$XDG_RUNTIME_DIR: the task, the team's stages (herdr_flow), each role's pane
and the worktree. Every role's socket filter and the team's orchestrator
(herdr-team-watch) read it and keep `state.json` beside it: the current
stage, what roles reported, failed checks and findings so far, who is
blocked, and whether the team has finished. Agents never see either file;
they reach them only through the filter's `team.*` methods.
"""

import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

MAX_MESSAGE = 4000
# Desktop notifications carry agent-written text; keep it short and inert.
MAX_NOTE = 300


def runtime_root():
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    root = Path(base) / "herdr-launch"
    root.mkdir(mode=0o700, exist_ok=True)
    return root


def runs_root():
    """Lasting run records, one JSON file per run (README "Runs and traces")."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "herdr-launch" / "runs"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def setup_of(paths):
    """Each setup file's sha256 (None when missing) and the version over all of
    them: sha256 of "path\0hash\0" sorted by path, first 12 hex digits."""
    files = {}
    for path in paths:
        try:
            files[str(path)] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError:
            files[str(path)] = None
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(f"{path}\0{files[path] or '<missing>'}\0".encode())
    return {"version": digest.hexdigest()[:12], "files": files}


def write_record(record):
    """Best effort: a run record that can't be written never stops a team."""
    try:
        root = runs_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{record['run_id']}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, indent=2))
        temporary.replace(path)
    except OSError:
        pass


def end_record(team, outcome, log):
    """Add how the run ended to its record (herdr-team-watch, at finish or stop)."""
    if "run_id" not in team:
        return
    try:
        record = json.loads((runs_root() / f"{team['run_id']}.json").read_text())
    except (OSError, ValueError):
        return
    record["outcome"] = {**outcome, "ended_at": utc_now()}
    record["log"] = log
    write_record(record)


# The pane metadata token that tells the GUI a role is blocked.
WAITING_SOURCE = "herdr-team"
WAITING_TOKEN = "team_waiting"


def create(workspace_id, branch, task, flow, members, worktree, setup_files=(), team_name=None):
    """Write a new team directory and the run's lasting record; return the
    directory. `flow` is herdr_flow.parse's {start, stages, limits};
    `setup_files` are the files that shaped the team (team file, role files,
    launcher settings), for the setup version; `team_name` is the team file's
    name (`tdd-team`)."""
    directory = Path(tempfile.mkdtemp(prefix=f"{workspace_id.replace(':', '-')}-", dir=runtime_root()))
    team = {
        "run_id": new_run_id(),
        "setup": setup_of(setup_files),
        "workspace_id": workspace_id,
        "branch": branch,
        "task": task,
        "flow": flow,
        "members": members,
        "worktree": worktree,
    }
    (directory / "team.json").write_text(json.dumps(team, indent=2))
    write_record({
        "run_id": team["run_id"],
        "team": team_name,
        "started_at": utc_now(),
        "setup": team["setup"],
        "workspace_id": workspace_id,
        "branch": branch,
        "task": task,
        "worktree": worktree,
        "roles": list(members),
        "outcome": None,
    })
    # stage: whose turn it is (None until the orchestrator starts the team);
    # events: what the stage's role reported, for the orchestrator to act on.
    save_state(directory, {"stage": None, "events": [], "counts": {}, "done": False, "stopped": None,
                           "summary": "", "waiting": None, "log": []})
    return directory


def load(directory):
    return json.loads((Path(directory) / "team.json").read_text())


def save_state(directory, state):
    path = Path(directory) / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2))
    temporary.replace(path)


@contextmanager
def state(directory):
    """Read-modify-write the shared state under an exclusive lock."""
    directory = Path(directory)
    with open(directory / "state.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = json.loads((directory / "state.json").read_text())
            yield current
            save_state(directory, current)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_state(directory):
    return json.loads((Path(directory) / "state.json").read_text())


def clean(text, limit):
    """Printable text only, at most `limit` characters."""
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def notify(title, body):
    """A desktop notification, at normal urgency (no sound); best effort,
    never through a shell."""
    body = clean(body, MAX_NOTE).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    command = ["notify-send", "--app-name=Herdr", clean(title, 100), body]
    try:
        subprocess.run(command, timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def log(directory, entry):
    with state(directory) as current:
        current["log"].append({"time": time.time(), **entry})
        current["log"] = current["log"][-200:]
