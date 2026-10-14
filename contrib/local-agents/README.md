# Local agent teams

`herdr-launch` starts a team of coding agents (Pi or OpenCode) on a new git
worktree, one herdr pane per role, each confined by `herdr-sandbox` (bubblewrap). The GUI's
**Launch team** dialog runs the same command.

```
herdr-launch --team app-team --repo ~/code/myapp --branch feature-login \
             --task "Add a login screen" [--base main] [--session NAME] [--json]
```

Install the GUI with `./install-gui.sh`: it builds this checkout and installs
`herdr-gpui` into `~/.local/bin` with a desktop entry and icon, so app
launchers list it as **Herdr**. Rerun it after pulling changes.

Install the launcher scripts with `./install.sh`. It copies the scripts to
`~/.local/share/herdr-launch`, links them into `~/.local/bin`, and seeds
`~/.config/herdr-launch/` with example configs it never overwrites:

- `teams/<team>.yaml`: `roles: [...]`, each matching `~/.config/opencode/agents/<role>.md`.
- `global.yaml`: folders every agent gets (`read_only`, `read_write`), plus `env` and `path`.
- `repos.yaml`: extra folders per repo.

## Pi or OpenCode

A team runs Pi or OpenCode: `agent: pi` or `agent: opencode` in the team
file, otherwise `agent` in `settings.yaml`, otherwise OpenCode. Both read the
same role files in `~/.config/opencode/agents/`. OpenCode gets the role by
name (`--agent`); for Pi, herdr-launch turns the front matter into `--model`
and `--thinking` (off when `chat_template_kwargs` disables thinking, else
medium) and passes the prompt with `--append-system-prompt`. Temperature and
step limits are not passed to Pi. `pi_command` in `settings.yaml` sets how Pi
starts; an absolute path pins a version. `sandbox_env` in `settings.yaml`
adds variables inside every sandbox, with `{agent}`, `{role}` and `{branch}`
filled in. Both agents run with their update checks and telemetry off (see
`OFFLINE_ENV` in `herdr_agents.py`).

## What a sandboxed agent can reach

- Its worktree and the repo's common `.git`, read-write; `.git/hooks` read-only.
- OpenCode: `~/.config/opencode` read-only; its data, state and cache read-write.
- Pi: a tmpfs at `~/.pi/agent` with each entry bound read-only on top (Pi
  writes lock files beside its config), `sessions/` read-write, and the
  folder Pi is installed in, read-only.
- `/usr` and a minimal `/etc`; granted folders; the network.
- Its own PID namespace: it cannot see your other processes.

The rest of the home directory is an empty tmpfs. herdr's control socket is
never bound in: whoever can write to it can run commands in any pane. A filter
socket is bound in instead, which forwards only `pane.report_agent`,
`pane.report_agent_session` and `pane.release_agent`, and only for the
agent's own pane. That is all the OpenCode and Pi herdr integrations send.

## Editing teams, agents and folder access

Herdr's **Settings → Teams** edits all of this; the **Manage teams...** link in
the Launch team dialog opens it. The GUI never writes these files itself:
`herdr-teams` (installed with the scripts) loads them as JSON and applies one
change at a time, validating the whole result with the same rules
`herdr-launch` uses before writing anything, and replacing files atomically.

```
herdr-teams load                    # everything, as JSON
echo '<change>' | herdr-teams apply # save_team, delete_team, save_agent,
                                    # delete_agent or save_access
```

Unknown keys in team and agent files are kept; YAML comments are not.
Renaming an agent updates every team that uses it; deleting one that a team
uses is refused.

## Picking repositories from GitHub

The Launch team dialog's GitHub button runs `herdr-repos` on the host:

```
herdr-repos list              # your repositories, with any local checkout
herdr-repos ensure OWNER/NAME # the checkout's path, cloned first if needed
```

It uses the GitHub CLI (`gh`) signed in on that host. Checkouts live in
`repos_dir` from `~/.config/herdr-launch/settings.yaml` (default `~/Repos`,
editable in Settings → Teams); a folder counts as the repository when its
`origin` remote points at it on GitHub. New clones go to `<repos_dir>/<name>`,
or `<repos_dir>/<owner>-<name>` if that name is taken.

## Teamwork

The team file's `flow` sets who works when. The launcher gives the task to the
first role only; each role hands on with the `team` command, available inside
its sandbox:

```
team info                    role, order, task
team status                  each teammate's state
team send <role> <message>   message a teammate (queued if it is busy)
team done <summary>          finish, and notify the user
```

Messages go through the filter, which sets the sender, only reaches this
team's panes, and caps messages back to an earlier role at `review_rounds`.
`herdr-team-watch` (started by the launcher) reminds an agent that stops
without handing off, and sends a desktop notification when an agent needs you
or the team is done. Team state lives under `$XDG_RUNTIME_DIR/herdr-launch/`,
outside every sandbox.

Tests (filter rules and herdr-teams, against a throwaway home):
`python3 -m unittest discover -s contrib/local-agents/tests`

Known gaps: through the shared `.git` an agent can move any branch of its
repo, and the network is open.

The OpenCode role prompts, OpenCode and herdr configuration, and the DGX
Spark inference setup the agents use are kept in the separate
`services-and-setups` repo (folders `OpenCode`, `Herdr`, `Spark`).
