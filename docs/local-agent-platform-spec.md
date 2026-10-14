# Local agent platform: implementation specification

Version 1, October 7, 2026. Written for Claude Code to implement end to end.

> Kept as the original brief; the code has moved on. Teams now run Pi by
> default as well as OpenCode (`contrib/local-agents/README.md`), and models,
> thinking and folder grants are edited in Settings → Teams.

## How to read this document

Section 2 lists decisions that are settled. Implement them as written and do not reopen them.

Two markers appear throughout:

- **VERIFY** means the detail came from documentation or a third-party project and has not been tested live. Check it against current docs or real behavior before building on it.
- **ASSUMPTION** means the detail came from the spec author's general knowledge, not a cited source. Treat it as a starting point and confirm it.

Items marked **BLOCKING** in section 9 decide whether the design works at all. If one fails, stop and report to the user before building further.

## 1. Goal

The user picks a team of agents (for example designer, coder, reviewer), a repo, a branch name and a task, all from one GUI. The system then:

1. creates a git worktree for the task on a new branch,
2. starts one OpenCode agent per role in its own herdr pane,
3. confines each agent to that worktree with bubblewrap,
4. hands every agent the task text.

All inference runs on a DGX Spark on the local network. After launch, the user watches and answers the agents in the same GUI.

## 2. Settled decisions

| Decision | Reason |
|---|---|
| Agent harness is OpenCode. | It talks to the local server over Chat Completions, which every local server supports. Codex CLI only speaks the Responses API, which local servers implement with known gaps. herdr also has a lifecycle integration for OpenCode, so it gets real agent state instead of screen scraping. |
| Inference is the user's existing setup on the DGX Spark: llama-swap in front of Qwen3.8 27B uncensored. | It already runs. This spec does not replace it. Section 4 lists what it must satisfy, and any change to it goes to the user first. |
| The Spark runs inference only. | Agents running on the Spark would compete with the model for its 128 GB unified memory. One community project had to cut vLLM's memory share from the 0.84 to 0.90 its recipes ship with down to 0.6 when the agent client ran there. |
| herdr runs natively on the dev PC, not in Docker. | herdr is one binary whose job is owning terminals where the repos and toolchains already live. Isolation comes from the per-agent sandbox instead. |
| There is one GUI: herdr-gpui, forked and extended with a Launch team dialog. | The user does not want to switch between two UIs. |
| The herdr workspace-manager plugin and layout YAML are not used. | Layouts are static per repo. Launches differ per task, so the launcher builds panes itself through the herdr CLI. |
| Each agent runs inside bubblewrap. | OpenCode's `external_directory` permission is not a boundary. Its file tools honor it but its bash tool does not, so shell redirects, `cp`, `python` and similar can write outside the workspace. |
| Folder grants live in config files outside every repo, keyed by repo. Team files hold roles only. | The same team gets sent at different repos. A grant file inside a repo could be edited by an agent with write access to widen its next sandbox. |
| The laptop uses herdr-gpui with the dev PC as a saved SSH device. | herdr-gpui supports saved SSH hosts. |

## 3. Architecture

```
Laptop (optional)
  herdr-gpui (fork) --SSH--+
                           |
Dev PC (Omarchy, Arch Linux, x86_64)
  herdr-gpui (fork, Launch team dialog)
     |  runs
     v
  herdr-launch (CLI)  -- reads -->  ~/.config/herdr-launch/{teams/,repos.yaml,global.yaml}
     |  herdr CLI                   ~/.config/opencode/{opencode.json,agents/*.md}
     v
  herdr server
     +-- workspace "feature-login" (git worktree of ~/code/myapp)
           +-- pane: herdr-sandbox -> opencode --agent designer
           +-- pane: herdr-sandbox -> opencode --agent coder
           +-- pane: herdr-sandbox -> opencode --agent reviewer
                       |
                       |  Chat Completions over LAN
                       v
DGX Spark
  llama-swap, OpenAI-compatible API  ->  backend  ->  Qwen3.8 27B uncensored
```

| Machine | Component | What it does |
|---|---|---|
| DGX Spark | llama-swap and its backend | Existing setup. llama-swap receives requests and routes them by model name to the backend serving Qwen3.8 27B uncensored. Nothing else runs here. |
| Dev PC | herdr server | Owns all workspaces, panes and agent processes. |
| Dev PC | herdr-gpui fork | The only UI. Shows workspaces and agents, and hosts the Launch team dialog. |
| Dev PC | `herdr-launch` | CLI that turns team, repo, branch and task into a worktree with sandboxed agents. The dialog calls it. |
| Dev PC | `herdr-sandbox` | Wrapper that starts one command inside bubblewrap with the grants for one repo. |
| Dev PC | OpenCode | The agent. One process per role per task. |
| Laptop | herdr-gpui fork | Same UI, connected to the dev PC over SSH. |

## 4. DGX Spark: existing inference

The Spark already runs llama-swap in front of Qwen3.8 27B uncensored. Do not replace or restructure this setup. Read its config, check it against the requirements below, and propose any change to the user before applying it.

### Start by reading the current setup

1. Find llama-swap's config file on the Spark and record:
   - the address and port it listens on,
   - the model name it uses for Qwen3.8 27B uncensored, which OpenCode must send in every request,
   - the command it runs for that model, which shows the backend and its flags.
2. Record any other models in the config.

### Requirements

1. **Reachable from the dev PC.** llama-swap's endpoint answers the dev PC, and a firewall rule limits it to the dev PC's IP.
2. **Context of at least 65,536 tokens per request.** Agents send long system prompts and tool schemas, and anything shorter silently breaks tool calling. **ASSUMPTION** if the backend is llama.cpp's `llama-server`, its total context (`-c`) is divided among its parallel slots (`--parallel`), so the total must be at least 65,536 times the slot count.
3. **Structured tool calling.** OpenCode depends on it. **ASSUMPTION** `llama-server` needs `--jinja` for this. Uncensored finetunes sometimes ship a modified chat template, so test tool calling with this exact model rather than trusting the base model's support.
4. **At least 9 concurrent requests,** which covers three agents on three tasks at once. **VERIFY** whether llama-swap passes concurrent requests for the same model straight to the backend, and how many the backend serves in parallel.
5. **No model swaps during agent work.** **ASSUMPTION** llama-swap unloads one model and loads another when a request names a different model, and can unload an idle model after a `ttl`. Every agent uses the same model name, so agents alone never trigger a swap. Report to the user if other clients use other models on this llama-swap, since each swap stalls every running agent. Also report any `ttl` set on this model, since the first request after an unload waits for a reload.

### Acceptance

- From the dev PC, `GET /v1/models` on llama-swap lists the model name recorded above.
- A `/v1/chat/completions` request to that model with a tool definition returns a structured `tool_calls` entry, not tool-call text inside the message content.
- A request with about 60,000 tokens of input succeeds.
- Nine parallel requests complete without errors.

## 5. Dev PC: agent stack

### 5.1 herdr

1. Install the current herdr release natively, following herdr.dev.
2. Run `herdr integration install opencode`. This installs the integration that reports OpenCode's state and session identity to herdr.
3. Confirm with `herdr agent` that OpenCode appears in the list of supported kinds.

### 5.2 OpenCode provider config

Install OpenCode, then point it at the Spark in `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "spark": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "DGX Spark",
      "options": {
        "baseURL": "http://SPARK_IP:LLAMA_SWAP_PORT/v1"
      },
      "models": {
        "LLAMA_SWAP_MODEL_NAME": { "name": "Qwen3.8 27B uncensored" }
      }
    }
  },
  "model": "spark/LLAMA_SWAP_MODEL_NAME"
}
```

Fill in the address, port and model name recorded in section 4. The model key must match llama-swap's model name exactly, because llama-swap routes on it. **ASSUMPTION** about OpenCode's key names. **VERIFY** against OpenCode's provider docs. If llama-swap requires an API key, add it to `options` and make sure `herdr-sandbox` passes it into the sandbox.

### 5.3 Role files

Each role is an OpenCode agent file in `~/.config/opencode/agents/<role>.md`. OpenCode reads global agents from that directory, and `opencode --agent <role>` starts the TUI with that agent. The file holds the role's model, system prompt and permissions.

Example `designer.md`:

```markdown
---
description: Designs screens and component specs for the task
mode: primary
model: spark/LLAMA_SWAP_MODEL_NAME
---
You are the designer on a team with a coder and a reviewer, all working in this
git worktree. For every task, write screen descriptions and component specs as
Markdown under design/. Do not edit application code.
```

Write `coder.md` and `reviewer.md` the same way. The prompts above are placeholders. The user will write the real ones.

**VERIFY** that `mode: primary` is required for an agent started with `--agent`, and which frontmatter key sets permissions. `opencode agent create` generates a correct file and is a quick way to check the format.

OpenCode's own permission settings stay at their defaults. They control what the agent asks before doing, not what it can reach. The sandbox decides reach.

### 5.4 Launcher configs

All launcher config lives in `~/.config/herdr-launch/`. This directory is outside every repo and is never bound into a sandbox, so agents can't read or change it.

`teams/<team>.yaml` lists roles and nothing else:

```yaml
team: app-team
roles: [designer, coder, reviewer]
```

`repos.yaml` lists extra folders for repos that need them. A repo with no entry gets nothing beyond its own worktree and `.git`, so most repos need no entry.

```yaml
- repo: ~/code/myapp
  read_write: [~/design-assets/myapp]
  read_only: [~/code/shared-ui]
```

`global.yaml` lists folders every agent needs regardless of repo, such as git identity and toolchains installed under the home directory:

```yaml
read_only:
  - ~/.gitconfig
  # Toolchains outside /usr, for example a mise or rustup directory, go here.
read_write: []
```

Validation rules:

- Every role named in a team must have a matching file in `~/.config/opencode/agents/`.
- Paths must exist. Expand `~` to the user's home.
- Refuse any grant that covers the home directory itself, `~/.ssh`, `~/.config/herdr-launch` or the `.git/hooks` of any repo.

### 5.5 `herdr-sandbox`

`herdr-sandbox` is a script that takes a worktree path and a command, and runs the command inside bubblewrap with exactly these binds:

| Path | Mode | Why |
|---|---|---|
| `/usr`, plus the `/bin`, `/sbin`, `/lib*` symlinks | read-only | System tools. The existing opencode-bwrap wrapper exposes the same set. |
| The task's worktree | read-write | The agent's working copy. |
| The repo's common `.git` directory | read-write | A linked worktree's `.git` is a pointer file into the main repo's `.git/worktrees/`, so git fails without it. |
| `<repo>/.git/hooks`, layered over the bind above | read-only | Worktrees share the repo's hooks. A hook an agent planted there would run with full user permissions the next time the user runs git in the main checkout. |
| `~/.config/opencode` | read-only | Role files and provider config. Read-only so an agent can't rewrite its own role. |
| `~/.local/share/opencode`, `~/.local/state/opencode`, `~/.cache/opencode` | read-write | OpenCode sessions, state and cache. Credentials live in `~/.local/share/opencode/auth.json`. |
| The OpenCode binary, if it lives outside `/usr` | read-only | So the agent can start. |
| Paths from `global.yaml` and the repo's `repos.yaml` entry | as listed | Grants from config. |
| herdr's socket | **VERIFY** whether needed | The OpenCode integration may report state through it. |

Further requirements:

- Network stays shared. The agent needs to reach the Spark. Restricting it further is out of scope for version 1.
- Pass through the environment variables herdr sets for panes, including `HERDR_PANE_ID` and `HERDR_WORKSPACE_ID`, plus llama-swap's API key variable if section 5.2 uses one.
- **ASSUMPTION** the base command needs `--proc /proc`, `--dev /dev`, `--tmpfs /tmp`, `--die-with-parent` and a minimal read-only set of `/etc` files for user lookup, DNS and TLS. Determine the exact `/etc` set by testing.
- **VERIFY** whether `--unshare-pid` breaks herdr's detection of the agent. If it does, leave the PID namespace shared.
- `.git/hooks` must be bound after the read-write `.git` bind so the read-only layer wins. **ASSUMPTION** about bubblewrap's bind ordering.

Acceptance, run from inside a sandboxed shell for a test repo:

- `ls ~` shows only the bound paths.
- `cat ~/.ssh/<any key>` fails because the file doesn't exist.
- Reading or writing another repo under `~/code` fails.
- `touch <repo>/.git/hooks/x` fails.
- Editing a file under `~/.config/opencode` fails.
- `git status`, `git add` and `git commit` succeed in the worktree.
- `curl` to the Spark's `/v1/models` succeeds.

### 5.6 `herdr-launch`

```
herdr-launch --team <team> --repo <path> --branch <name> --task <text> [--json]
```

Steps:

1. **Validate.** Load the team, `repos.yaml` and `global.yaml`, and apply section 5.4's rules. Check that the repo is a git repo and that the branch name is valid. **VERIFY** what `herdr worktree create` does when the branch already exists, and refuse that case if it misbehaves.
2. **Create the worktree.** Run `herdr worktree create --cwd <repo> --branch <branch>`. **VERIFY** the output format. A third-party herdr client reads the new workspace's root pane from `result.root_pane`. Get the worktree path from the output, or from `git worktree list --porcelain` if the output lacks it. Get the common `.git` directory with `git rev-parse --git-common-dir`.
3. **Create one pane per role.** The first role uses the root pane. For each later role, run `herdr pane split <pane> --direction right --no-focus --cwd <worktree>` and read the new pane ID from `.result.pane.pane_id`.
4. **Start each agent.** Run `herdr pane run <pane> "<command>"`, where the command is `herdr-sandbox <worktree> -- opencode --agent <role> --prompt <task>`. Quote the task text safely. It is free text and may contain quotes and newlines, so build the command with a proper shell-quoting function and never with string concatenation.
5. **Confirm herdr recognizes the agent.** Wait on each pane with `herdr wait agent-status <pane> --status <state>`, with a timeout. **VERIFY** which status values and flags apply. This step proves herdr detects OpenCode inside the sandbox.
6. **Deliver the task if needed.** **VERIFY** whether OpenCode's `--prompt` flag submits the prompt or only fills the input box. If it only fills it, drop `--prompt` from step 4. Instead, once each agent is ready, run `herdr agent prompt <pane> "<task>"`. herdr's agent commands accept a pane ID in place of an agent name.
7. **Report.** Print the workspace ID, worktree path and a role-to-pane mapping. With `--json`, print it as JSON for the GUI.

Do not use `herdr agent start`. It launches the agent binary itself, so it can't run OpenCode inside the sandbox.

On failure, stop and leave whatever was created in place for inspection. Report which step failed and the command's output. Do not delete the worktree automatically.

Nice to have: give each pane its role as a title, if the herdr CLI supports renaming panes. **VERIFY**.

## 6. Dev PC: herdr-gpui fork

### 6.1 Base

- Fork `penso/herdr-gpui`, licensed Apache-2.0. It is an unaffiliated, experimental project, so keep every change in its own module and keep the diff small enough to rebase. Offering the dialog upstream as a pull request is optional.
- Build on the dev PC following the repo's Linux instructions. Runtime needs a Vulkan driver, the ALSA shared library with a configured default audio device, and glibc 2.39 or newer.
- On Linux, herdr-gpui maps its Cmd shortcuts to Super, which can clash with desktop shortcuts. Omarchy's Hyprland bindings are Super-based (**ASSUMPTION**). Rebind the clashing commands in `config-gpui.local.toml` under `[keybindings]`.

### 6.2 Launch team dialog

Entry points:

- a "Launch team" command in the command palette, with a keybinding,
- a "Launch team here..." item in the right-click menu of a repo's main-checkout workspace, which pre-fills the repo.

Fields:

| Field | Source | Validation |
|---|---|---|
| Team | Files in `~/.config/herdr-launch/teams/` | Must exist |
| Repo | Pre-filled from the clicked workspace, or picked from main-checkout workspaces herdr knows | Must be a git repo |
| Branch | Free text | Reuse the git branch-name validation from the existing New worktree dialog |
| Task | Multiline free text | Not empty |

Behavior:

- Follow the existing New worktree dialog for layout, IME handling, keyboard behavior and inline errors.
- On submit, run `herdr-launch --json ...` off the UI thread. herdr-gpui already keeps file, process and SSH work off the UI thread.
- Show progress in the dialog. On failure, show the failing step and its output inline and keep the dialog open.
- On success, close the dialog and focus the new workspace.
- When the selected device is a saved SSH host, run `herdr-launch` on that host over noninteractive SSH. herdr-gpui's Teleport and session deletion already run herdr commands on remote hosts this way. `herdr-launch` and the configs must be installed on that host.

## 7. Laptop

1. Install the fork build of herdr-gpui.
2. Set up noninteractive SSH from the laptop to the dev PC with a key, and trust the host key once in a terminal.
3. In herdr-gpui, use Add Device with the dev PC's SSH target.
4. Launching from the laptop uses section 6.2's remote path, so `herdr-launch` runs on the dev PC, where the repos and configs live.

## 8. Security model

What an agent launched on repo `myapp` for branch `feature-login` can reach:

| Reachable | Not reachable |
|---|---|
| The `feature-login` worktree, read-write | The main checkout's working files |
| The myapp `.git`, read-write, except `hooks/`, which is read-only | Other repos |
| Extra folders from `repos.yaml` and `global.yaml` | The rest of the home directory, including `~/.ssh` |
| `/usr` and the minimal `/etc` set | `~/.config/herdr-launch` |
| OpenCode's config, read-only, and its data directories, read-write | Its own role file, for writing |
| The network | |

Known gaps, accepted for version 1:

- Through the shared `.git`, an agent can change any branch of its repo, not only its own.
- The network is open, so an agent can reach anything the dev PC can.
- An agent can read OpenCode's `auth.json` and any llama-swap API key in OpenCode's config. This matters only on the LAN.

## 9. Open questions

| # | Question | Blocking | How to check |
|---|---|---|---|
| 1 | Does herdr recognize OpenCode running inside bubblewrap, so that `wait agent-status` and `agent prompt` work by pane ID? | **BLOCKING** | Run step 5.6.4 manually in a pane, then query its state. |
| 2 | Does the OpenCode integration report state from inside the sandbox, and does it need herdr's socket bound in? | **BLOCKING** for real agent state | Compare herdr's state for a sandboxed and an unsandboxed OpenCode. |
| 3 | What JSON does `herdr worktree create` return, and does it include the root pane and the worktree path? | No | Run it once. |
| 4 | Does `opencode --prompt` submit the prompt or only pre-fill it? | No, step 5.6.6 covers both | Run it once. |
| 5 | Does `--agent` need `mode: primary`, and what is the permissions frontmatter key? | No | `opencode agent create`, then inspect the file. |
| 6 | Does Qwen3.8 27B uncensored, as served behind llama-swap, return structured tool calls? | **BLOCKING** | Section 4's tool-call acceptance test. If it fails, report the backend flags and chat template to the user before changing anything. |
| 6a | How many parallel requests does the backend serve for this model, and at what context per request? | No, unless it is under 9 requests at 65,536 tokens | Read llama-swap's config, then section 4's acceptance tests. |
| 7 | Does `--unshare-pid` break agent detection? | No | Test both ways. |
| 8 | Does `pane run` need the new pane's shell prompt to be ready first, and how long does that take? | No | Test right after `pane split`. |
| 9 | Can the herdr CLI rename panes? | No | `herdr pane --help`. |
| 10 | Does the fork build and render correctly on Omarchy under Hyprland? | **BLOCKING** for the GUI | Build it and run a smoke test against the live daemon. |

## 10. Implementation phases

Finish each phase and pass its acceptance checks before starting the next.

1. **Check the Spark's existing inference.** Section 4. Read llama-swap's config and pass section 4's acceptance tests. Propose any needed change to the user before applying it.
2. **Unsandboxed agent.** Sections 5.1 to 5.3. In a herdr pane, run `opencode --agent coder` against a test repo. It completes a small edit using tool calls on the Spark, and herdr shows its state.
3. **Sandbox.** Section 5.5. Pass section 5.5's acceptance tests. Resolve open questions 1, 2 and 7. If question 1 or 2 fails, stop and report to the user.
4. **Launcher CLI.** Sections 5.4 and 5.6. `herdr-launch --team app-team --repo <test repo> --branch test-1 --task "Add a README section"` produces a workspace with three working, sandboxed agents that all received the task.
5. **GUI.** Section 6. The dialog does the same as phase 4, and the new workspace and its agents appear and are usable in herdr-gpui.
6. **Laptop.** Section 7. Launch from the laptop against the dev PC, and work with the agents from there.

## 11. Sources

- herdr agent skill and CLI patterns: https://github.com/herdrdev/herdr/blob/master/skills/herdr/SKILL.md
- herdr agent automation docs, covering `pane split`, `agent start`, `agent prompt` and pane-ID targeting: https://github.com/herdrdev/herdr/blob/master/docs/versions/0.8.0/website/src/content/docs/agent-automation.mdx
- Root-pane field from `worktree create`, as read by a third-party client: https://github.com/brogrammerMW/herdr-voice/pull/71
- herdr-gpui: https://github.com/penso/herdr-gpui and https://github.com/penso/herdr-gpui/blob/main/crates/herdr-gpui/README.md
- OpenCode CLI flags: https://opencode.ai/docs/cli/
- OpenCode agents: https://opencode.ai/docs/agents/
- OpenCode bash tool bypassing `external_directory`: https://github.com/anomalyco/opencode/issues/42979
- Request for a real OpenCode filesystem sandbox: https://github.com/anomalyco/opencode/issues/21733
- Existing bubblewrap wrapper for OpenCode: https://github.com/dontdrinkandroot/opencode-bubblewrap
