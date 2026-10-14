"""Network filter for sandboxed agents: which slice they run in, and whether
the host's filter for it is active.

herdr-sandbox starts every agent in the systemd user slice herdr-agents.slice
(installed by install.sh, kept alive by herdr-agents-anchor.service). A root
service on the host matches that slice's cgroup in nftables and decides where
agents may connect. nftables resolves the cgroup path to its id once, when the
rules load, so the service writes the id it loaded to ID_FILE. If the slice is
recreated (a new id), its rules no longer match; herdr-sandbox then refuses to
start an agent rather than run it unfiltered.

`network_filter` in settings.yaml: `required` (default) or `off`, for hosts
without the root service.
"""

import os
from pathlib import Path

SLICE = "herdr-agents.slice"
ID_FILE = Path("/run/herdr-agents-net/cgroup-id")
CGROUP_ROOT = Path("/sys/fs/cgroup")


def slice_dir(uid=None):
    uid = os.getuid() if uid is None else uid
    # A dash in a slice name nests it: herdr-agents.slice lives in herdr.slice.
    return CGROUP_ROOT / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service" / "herdr.slice" / SLICE


def mode(settings):
    value = settings.get("network_filter", "required") if isinstance(settings, dict) else "required"
    if value not in ("required", "off"):
        raise ValueError(f"network_filter must be 'required' or 'off', not {value!r}")
    return value


def problem(id_file=ID_FILE, cgroup=None):
    """Why agents would run unfiltered, or None when the filter is active."""
    cgroup = slice_dir() if cgroup is None else cgroup
    try:
        current = cgroup.stat().st_ino
    except FileNotFoundError:
        return f"{cgroup} does not exist; start it with: systemctl --user start herdr-agents-anchor"
    try:
        loaded = int(id_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        return f"no network filter is loaded ({id_file} missing); start it with: sudo systemctl start herdr-agents-net"
    if loaded != current:
        return f"the network filter matches an old {SLICE}; reload it with: sudo systemctl restart herdr-agents-net"
    return None


def wrap(command):
    """Run `command` in a transient scope inside the agents' slice."""
    return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--slice={SLICE}", "--", *command]
