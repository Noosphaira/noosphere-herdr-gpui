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
- `/usr` and a minimal `/etc`; granted folders; the network, as far as the
  host's filter allows (below).
- Its own PID namespace: it cannot see your other processes.

Network: herdr-sandbox starts every agent in the systemd user slice
`herdr-agents.slice` (installed by `install.sh`, with
`herdr-agents-anchor.service` keeping its cgroup in place). A root service on
the host matches that cgroup in nftables and decides where agents may
connect, and writes the cgroup id it loaded to
`/run/herdr-agents-net/cgroup-id`. herdr-sandbox refuses to start an agent
when that file is missing or names an older cgroup, so a stale filter never
means an unfiltered agent. `network_filter: off` in `settings.yaml` turns the
check off on hosts without such a service. The rules themselves are
site-specific (which hosts and ports agents need) and are not part of this
repository; `herdr_netfilter.py` describes the contract.

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

## Role tools, scenarios and reports

`team-tools/team_tools.py` holds the checks the TDD teams' roles run inside
the sandbox (`setup-env`, `brief-lint`, `red-check`, `ac-trace`, `check`,
`review-pack`, ...; `team_tools.py` with no arguments lists them). The
planner writes the acceptance criteria as Gherkin scenarios, one per
criterion and tagged `@AC-n`, in the `.feature` files the brief's FEATURES
section names. The test author binds them to step code with pytest-bdd or
`@amiceli/vitest-cucumber` when the repo has one as a dev dependency, and
with plain tests citing `AC-n` when it doesn't. Only `plan:` commits may
change a `.feature` file.

`setup-env`, `red-check` and `check` also leave Allure results in the
worktree's `.team/allure/`. On the host, `team-report [WORKTREE...] [--open]`
adds every run not yet reported to that repo's history and writes a
single-file Allure report to `~/.local/share/herdr-launch/reports/<repo>/report/`.
The Allure CLI it uses is pinned in `install.sh`; agents never get it.

Tests (filter rules, herdr-teams and the role tools, against throwaway repos):
`python3 -m unittest discover -s contrib/local-agents/tests`

Known gaps: through the shared `.git` an agent can move any branch of its
repo, and the network is open.

The OpenCode role prompts, OpenCode and herdr configuration, and the DGX
Spark inference setup the agents use are kept in the separate
`services-and-setups` repo (folders `OpenCode`, `Herdr`, `Spark`).
