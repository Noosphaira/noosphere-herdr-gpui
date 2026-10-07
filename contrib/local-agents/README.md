# Local agent teams

`herdr-launch` starts a team of OpenCode agents on a new git worktree, one
herdr pane per role, each confined by `herdr-sandbox` (bubblewrap). The GUI's
**Launch team** dialog runs the same command.

```
herdr-launch --team app-team --repo ~/code/myapp --branch feature-login \
             --task "Add a login screen" [--base main] [--session NAME] [--json]
```

Install with `./install.sh`. It copies the scripts to
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

Known gaps: through the shared `.git` an agent can move any branch of its
repo, and the network is open.
