# Local agent teams

`herdr-launch` starts a team of OpenCode agents on a new git worktree, one
herdr pane per role, each confined by `herdr-sandbox` (bubblewrap). The GUI's
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

## What a sandboxed agent can reach

- Its worktree and the repo's common `.git`, read-write; `.git/hooks` read-only.
- `~/.config/opencode` read-only; OpenCode's data, state and cache read-write.
- `/usr` and a minimal `/etc`; granted folders; the network.
- Its own PID namespace: it cannot see your other processes.

The rest of the home directory is an empty tmpfs. herdr's control socket is
never bound in: whoever can write to it can run commands in any pane. A filter
socket is bound in instead, which forwards only `pane.report_agent`,
`pane.report_agent_session` and `pane.release_agent`, and only for the
agent's own pane. That is all OpenCode's herdr integration sends.

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

Tests: `python3 -m unittest discover -s contrib/local-agents/tests`

Known gaps: through the shared `.git` an agent can move any branch of its
repo, and the network is open.

The OpenCode role prompts, OpenCode and herdr configuration, and the DGX
Spark inference setup the agents use are kept in the separate
`services-and-setups` repo (folders `OpenCode`, `Herdr`, `Spark`).
