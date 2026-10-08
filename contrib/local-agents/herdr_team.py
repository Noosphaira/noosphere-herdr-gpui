"""A launched team's shared files, outside every sandbox.

herdr-launch writes `team.json` into a private directory under
$XDG_RUNTIME_DIR: the task, the order roles work in, and each role's pane.
Every role's socket filter and the launcher's watcher read it and keep
`state.json` beside it: whose turn it is, how many review rounds have been
used, and whether the team has finished. Agents never see either file; they
reach them only through the filter's `team.*` methods.
"""

import fcntl
import json
import os
import re
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


def create(workspace_id, branch, task, flow, members, review_rounds):
    """Write a new team directory and return its path."""
    directory = Path(tempfile.mkdtemp(prefix=f"{workspace_id.replace(':', '-')}-", dir=runtime_root()))
    team = {
        "workspace_id": workspace_id,
        "branch": branch,
        "task": task,
        "flow": flow,
        "members": members,
        "review_rounds": review_rounds,
    }
    (directory / "team.json").write_text(json.dumps(team, indent=2))
    save_state(directory, {"active": flow[0], "rounds": 0, "done": False, "summary": "", "log": []})
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


def is_feedback(flow, sender, recipient):
    """A message back to an earlier role is a review round."""
    return flow.index(recipient) < flow.index(sender)


def notify(title, body, urgent=False):
    """A desktop notification; best effort, never through a shell."""
    body = clean(body, MAX_NOTE).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    command = ["notify-send", "--app-name=Herdr", clean(title, 100), body]
    if urgent:
        command.insert(1, "--urgency=critical")
    try:
        subprocess.run(command, timeout=5, check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        pass


def log(directory, entry):
    with state(directory) as current:
        current["log"].append({"time": time.time(), **entry})
        current["log"] = current["log"][-200:]
