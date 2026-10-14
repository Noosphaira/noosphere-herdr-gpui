"""Which coding agent a team runs, and what that agent needs.

Role files stay in OpenCode's format for both agents
(~/.config/opencode/agents/<role>.md: YAML front matter + prompt), so the
Teams settings edit one set of roles:

- opencode: `opencode --agent <role>` reads the file itself.
- pi: Pi has no named agents, so the launcher turns the front matter into
  flags (`--model`, `--thinking`) and hands the prompt over as a file the
  sandbox binds at PROMPT_IN_SANDBOX (`--append-system-prompt`).

A team picks its agent with `agent: pi` in its team file (default opencode).
"""

import shlex
from pathlib import Path

import yaml

HOME = Path.home()
AGENTS = ("opencode", "pi")
DEFAULT = "opencode"
AGENTS_DIR = HOME / ".config/opencode/agents"
PROMPT_IN_SANDBOX = "/run/herdr-team/prompt.md"

# Per agent, by executable name: config the sandbox binds read-only, data it
# binds read-write (later binds win, so a read-write folder inside a
# read-only one stays writable), and whether the executable needs the files
# beside it (Pi's install folder holds its assets).
#
# "entries_read_only" folders become a writable tmpfs with each existing entry
# bound read-only on top: Pi creates lock files beside its config
# (settings.json.lock), but must not change the config or its extensions.
SANDBOX_DIRS = {
    "opencode": {
        "read_only": [".config/opencode"],
        "entries_read_only": [],
        "read_write": [".local/share/opencode", ".local/state/opencode", ".cache/opencode"],
        "install_dir": False,
    },
    "pi": {
        "read_only": [],
        "entries_read_only": [".pi/agent"],
        "read_write": [".pi/agent/sessions"],
        "install_dir": True,
    },
}

# Each agent's own outbound calls, all off. Inference goes only to the
# provider in its config. Set for every agent; the other's are inert.
OFFLINE_ENV = {
    # OpenCode: update checks, conversation sharing, the models.dev catalog,
    # language-server and plugin downloads.
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    # Pi: model catalog, version and package checks, fd/rg downloads
    # (PI_OFFLINE), install/update telemetry.
    "PI_OFFLINE": "1",
    "PI_TELEMETRY": "0",
    "PI_SKIP_VERSION_CHECK": "1",
}


class AgentError(Exception):
    pass


def sandbox_dirs(executable):
    """The SANDBOX_DIRS entry for an executable; OpenCode's for anything else."""
    return SANDBOX_DIRS.get(Path(executable).name, SANDBOX_DIRS["opencode"])


def read_role(role, agents_dir=AGENTS_DIR):
    """(front matter, prompt) of a role file."""
    path = agents_dir / f"{role}.md"
    text = path.read_text()
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            meta = yaml.safe_load(text[4:end]) or {}
            if isinstance(meta, dict):
                return meta, text[end + 4:].lstrip("\n")
    return {}, text


def role_command(agent, role, pi_command="pi", agents_dir=AGENTS_DIR):
    """The command for one role, and the prompt to bind (None for OpenCode)."""
    if agent == "opencode":
        return ["opencode", "--agent", role], None
    if agent != "pi":
        raise AgentError(f"unknown agent {agent!r}; use one of {', '.join(AGENTS)}")
    meta, prompt = read_role(role, agents_dir)
    model = str(meta.get("model") or "").strip()
    if not model:
        raise AgentError(f"{role}: the role file names no model")
    kwargs = meta.get("chat_template_kwargs") or {}
    thinking = kwargs.get("enable_thinking", True) if isinstance(kwargs, dict) else True
    command = shlex.split(pi_command) + [
        "--model", model,
        # Pi sends enable_thinking per its thinking level (qwen-chat-template).
        "--thinking", "medium" if thinking else "off",
    ]
    if prompt.strip():
        command += ["--append-system-prompt", PROMPT_IN_SANDBOX]
    return command, prompt
