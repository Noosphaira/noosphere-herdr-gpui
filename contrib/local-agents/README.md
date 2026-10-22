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

The team file's `stages` say who works when and what decides the next step
(`herdr_flow.py`; a file with only `flow` gets one stage per role in order):

```yaml
start: plan
stages:
  plan:   {role: planner,      next: red}
  red:    {role: test-author,  check: red-check, pass: green,  fail: red}
  green:  {role: tdd-coder,    check: check,     pass: review, fail: green}
  review: {role: tdd-reviewer, check: review-check, pass: finish, fail: review, findings: green}
limits: {fail: 3, findings: 3}
```

Agents never hand work on. They report with the `team` command, available
inside the sandbox:

```
team info                     your stages, whose turn it is, the task
team status                   each teammate's state
team done <summary>           your turn is done and committed
team findings <findings>      (stages with `findings`) send findings on
team blocked <what failed>    you can't go on: notify the user, mark the pane
team ask <role> <question>    ask a teammate; it doesn't change whose turn it is
team answer <role> <answer>   answer a teammate
```

Only the role whose turn it is can report done or findings. The orchestrator
(`herdr-team-watch`, started by the launcher, outside every sandbox) acts on
each report: it refuses a `team done` with uncommitted changes, runs the
stage's check inside the sandbox (`herdr-sandbox`, never on the host; the
check finds the role's summary in `$TEAM_REPORT`; full output in `checks/`
of the team directory), and prompts the role of the next
stage with the summary, the findings, or the failed check's output. More
than `limits.fail` failed checks in a stage, or `limits.findings` findings in
all, stop the team; `finish` ends it. It reminds a role that goes idle
without reporting once, then notifies the user. Team state, including the log
of every move, lives under `$XDG_RUNTIME_DIR/herdr-launch/`.

The GUI tints and frames red a pane whose agent is blocked on an approval or
ran `team blocked` (the `team_waiting` pane token; cleared when the agent
works again after the user answered), and tints and frames in the accent
color the tab's working agent, or the one that last changed state, when the
tab shows several agents.

## Runs and traces

Every launch is a run with its own id (`20261021T201500Z-a1b2c3`: UTC start
time and six random hex digits) and a setup version: the first 12 hex digits
of a sha256 over the files that shaped the team, each as its path and its
content's sha256 (sorted by path; a missing file counts as `<missing>`). The
files are the team file, each role's agent file, `settings.yaml`,
`global.yaml` and `repos.yaml`. Same files, same version; any edit, a new one.

The launcher writes a lasting record of the run to
`~/.local/share/herdr-launch/runs/<run id>.json` (`$XDG_DATA_HOME`): the id,
team (the team file's name), start time, setup version with each file's
hash, workspace, branch, task, worktree and roles. The orchestrator adds the
outcome when the team ends (`finished` with the last summary, or `stopped`
with the reason, plus the end time and the log of moves). A failed check's
log entry keeps the last 2,000 characters of its output, since the team
directory with the full output is gone after a reboot. A record that can't be
written never stops a team. The improvement pipeline (noosphere-mastra-workflows,
setup kind `coding-teams`) reads these records.

Every agent of the run gets `HERDR_TEAM_RUN` and `HERDR_SETUP_VERSION` in its
environment, and `sandbox_env` values may use `{run}`. Pi's Opik extension
(services-and-setups `Pi/extensions/opik-trace.ts`) puts both on each trace,
as metadata `run_id` and `setup_version` and as tags `run:<id>` and
`setup:<version>`, so a run's traces can be found together in Opik.

## Role tools, scenarios and reports

`team-tools/team_tools.py` (with `team_repo.py`, `team_gherkin.py` and
`team_allure.py` beside it) holds the checks the TDD teams' roles run inside
the sandbox (`setup-env`, `brief-lint`, `red-check`, `ac-trace`, `check`,
`review-pack`, `review-check`, ...; `team_tools.py` with no arguments lists them). The
planner writes the acceptance criteria as Gherkin scenarios, one per
criterion and tagged `@AC-n`, in the `.feature` files the brief's FEATURES
section names. The test author binds them to step code with pytest-bdd or
`@amiceli/vitest-cucumber` when the repo has one as a dev dependency, and
with plain tests citing `AC-n` when it doesn't. Only `plan:` commits may
change a `.feature` file. `red-check` judges only the tests the branch adds or
changes: each must fail on an assertion and reach one of the brief's ENTRY
POINTS (what the task's user calls), and every criterion needs one failing
test; others of that criterion may pass against the stub. `review-check` fails while an OWNED PATH of the
brief is unchanged and the reviewer's summary doesn't name it with a reason:
passing tests don't show that the brief's planned work was done.

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
