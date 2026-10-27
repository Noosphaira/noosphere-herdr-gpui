# AGENTS.md

<!-- BEGIN UNIVERSAL: maintained in the rules repository and synced. Edit it there. -->
## Universal rules

### Instruction files
- Do not create `CLAUDE.md`, `.claude/CLAUDE.md` or `CLAUDE.local.md` in any directory of this repository. Put instructions in AGENTS.md.
- Do not edit the Universal rules section (between the BEGIN UNIVERSAL and END UNIVERSAL comments). Project instructions go in the Project section.
- The Project section adds to the universal rules. A deviation is listed under "Exceptions to universal rules" and names the rule it changes.

### Setup
- Before the first commit in a session, run `git config core.hooksPath`. If the output is not `.githooks`, run `git config core.hooksPath .githooks`.
- Push, open pull requests and merge as the GitHub App `noosphere-agent`. The SessionStart hook sets `GH_TOKEN` to the App's token. Before the first push in a session, run `gh api /installation/repositories --silent`. If it fails, stop and report a blocker. Do not push with the user's credentials.

### Branches
- `master` holds released code. `develop` is the integration branch and the default branch.
- Never commit to `develop` or `master`, push to them, or rewrite their history.
- Create every new branch from the latest `origin/develop`.
- Branch name: `<category>/<description>`, lowercase, words separated by hyphens. Example: `feature/csv-export`.

| Category | Contents |
|---|---|
| `feature` | New functionality, or a change to existing functionality |
| `bugfix` | Fix for a defect |
| `documentation` | Changes to documentation only |
| `refactor` | Code restructuring with no change in behavior |
| `test` | Changes to tests only |
| `chore` | Dependency updates, build, CI and tooling configuration |

### Commits
- Subject line: imperative mood, at most 72 characters. Then a blank line and a body stating what changed and why.
- A commit with AI-generated changes ends with the trailer `Assisted-by: <agent>:<model>`. Example: `Assisted-by: claude-code:claude-opus-5-5`.
- No `Co-authored-by` or `Signed-off-by` trailer names an AI agent.
- Never run git with `--no-verify`.
- After a pull request is opened, do not rewrite the branch history. To update the branch, merge `origin/develop` into it.

### Work cycle
1. Create a branch from `develop`, implement the change, verify it.
2. Work is ready for user review when a person can use the change in its interface: a changed screen in the frontend, a command that runs in the terminal, an endpoint that responds. Code that compiles does not meet this.
3. Work with an open decision or blocker is not ready for user review. Present each decision and blocker to the user and wait for the answer.
4. When the work is ready: commit, run the change locally (Project > Run locally) and present it to the user. Do not push and do not open a pull request.
5. After the user approves: push the branch, open a pull request to `develop`, and follow the `pr-review-loop` skill until the pull request is merged.
   Every merge into `develop` is made as the App. A pull request that is ready to merge is not left open: merge it, or tell the user what it waits for. When the user wants to merge a pull request into `develop` with their own account, tell them first that they then cannot approve the next release pull request to `master`: the `master` ruleset requires an approval from someone other than the last pusher.
6. After the merge: open or update the pull request from `develop` to `master`. Merging into `master` requires the user's approval on GitHub. Merge into `master` only when the user asks.

The `git-workflow` skill contains the commands for each step.

### Verification
- Before user review, run the build, tests and linters listed in Project > Build, test and lint. The change adds no new warnings.
- Reproduce a bug before fixing it. Where the project has tests, add a test that fails without the fix.
- State what could not be built, run or tested.

### Change size
- Stop and ask the user before a change exceeds 10,000 added plus deleted lines, excluding lockfiles.
- A pull request to `develop` over this limit fails the `pr-size` check unless it has the `size-exception` label. Add the label only after the user approves the size.
<!-- END UNIVERSAL -->

## Project

### Overview
Herdr GPUI is a native Rust/GPUI client for macOS, Linux and Windows. It connects to an existing local Herdr daemon. It is not a terminal emulator, server or TUI wrapper. Herdr owns terminal processes and session state. Closing or detaching the GUI leaves the daemon and its terminals running.

Read this file with `README.md`, the relevant crate documentation and the code before making changes.

#### Fork
- This repository, `Noosphaira/noosphere-herdr-gpui`, began as a fork of `penso/herdr-gpui` and is now a separate project. All work happens here: branches, pushes, pull requests and every `gh` command.
- Never use `penso/herdr-gpui`: no pull requests, issues, comments or pushes there, and no fetching, syncing, merging or rebasing from it.
- Pass `--repo Noosphaira/noosphere-herdr-gpui` to `gh`. `gh` may otherwise default to the original parent.
- The repository has no CI workflows, release pipeline or in-app updater. The local `just` recipes are the validation.
- Keep `LICENSE`, `NOTICE` and the attribution files (`crates/herdr-protocol/NOTICE.md`, `crates/herdr-gpui/GITHUB-NOTICE.md`, `crates/herdr-gpui/SOUND-NOTICE.md`). Apache-2.0 requires them.

#### Priorities
- Keep changes small, explicit and focused on the root cause.
- Preserve user and other agents' changes. Never revert unrelated edits.
- Prefer existing standard traits and generics over unnecessarily concrete APIs.
- Protect protocol compatibility, bounded resource use and UI responsiveness.
- Add regression tests for changed behavior and report what was actually verified.
- Never modify or patch Herdr itself. An unrelated external team owns it. Sibling Herdr checkouts are read-only references. Propose an upstream pull request only when genuinely justified. Never make incidental upstream edits for this client.

#### Architecture

| Location | Responsibility |
| --- | --- |
| `crates/herdr-protocol` | Generation-1 wire types, framing, validation, and atomic surface patches |
| `crates/herdr-client` | Discovery, socket worker, session transitions, ordered commands, and client-local activity projection |
| `crates/herdr-gpui` | Native UI, connection bridge, presentation state, semantic input, geometry, and painting |
| `crates/test-support/sandbox.rs` | Shared isolated process setup for opt-in integration tests; not a production crate |

- Keep dependency direction from UI to client to protocol. Protocol and client code must not depend on GPUI.
- Keep session transitions separate from socket scheduling, and connection ownership separate from window rendering.
- Reuse `ConnectionBridge`, domain targets, geometry helpers and the test sandbox. Do not duplicate their policies.
- Organize files by responsibility, not by length. There is no line limit. A file holds one concern you can name in a sentence, and its module doc comment says what it owns. Split when a file holds two: state and its rendering, parsing and I/O, one feature's code or tests beside an unrelated feature's. Length is a reason to look, not to cut. A long file with one cohesive job (a protocol table, a painter, a provider list) stays whole. A short file that mixes concerns gets split.
- To split, move a whole responsibility into a child module (`foo.rs` plus `foo/bar.rs`). Leave the parent as wiring and re-exports. Keep moved items private or `pub(super)`. Make the split its own commit ahead of the change that needs it. Do not reshuffle unrelated files.
- Keep APIs narrow. Use private items by default and `pub(super)` or `pub(crate)` only where needed. Do not expose every field merely to ease extraction.
- Define shared types once in the lowest appropriate layer and re-export them. Do not create mirror enums or convert between them through strings.
- Do not add a crate, runtime, service container or speculative transport abstraction without a concrete need. Sibling checkouts are references, not build dependencies.

### Build, test and lint
`.github/workflows/ci.yml` runs the format, lint and test steps below on every pull request (job `format, lint and test`). A repository ruleset requires it on `develop` and `master`.

Use the `just` recipes. Run focused tests while iterating. Before user review of a Rust change, run:

```sh
just format
just ci
```

- `just ci` runs `format-check`, `lint` (clippy, all targets and features, `-D warnings`) and `test` (default and all features).
- For linking, startup or packaging changes, also run `just test-build`. It builds the release executable and exercises its CLI without a desktop.
- For a change that touches a `cfg` gate, also run `just lint-linux` and `just lint-windows`. A `cfg` gate is only proven on the platform it names.
- Documentation-only changes need command, path and link review and `git diff --check`.

### Run locally
- `just run` starts the optimized release build with the QA menu. `just run-debug` starts a debug build and is slower with a dense terminal on screen.
- The app needs an installed Herdr daemon. It starts an already-installed local `herdr server` when the target session is absent. It never installs or upgrades a daemon.
- On macOS both recipes build a local bundle identified as `so.pen.herdr-gpui.dev`.
- Measure interactive performance with `just run`, not a debug build.

### Repository layout
- `crates/`: the workspace crates (see Overview > Architecture).
- `assets/`: icons, platform bundle files and the Linux desktop entry.
- `scripts/`: Linux dependencies, lint containers, icon generation and `build-icon.py`, which picks the bundle icon from a binary's build identity.
- `docs/`: user and platform documentation.
- `.githooks/`: the git hooks set by `core.hooksPath`.
- `.claude/skills/`: agent skills (`git-workflow`, `pr-review-loop`, `gpui-mockup`).

### Conventions

#### Rust conventions
- Require only the capabilities an operation uses. Prefer existing traits such as `Read`, `Write`, `IntoIterator`, `AsRef<Path>` and `AsRef<OsStr>` over specific streams, collections or owned paths.
- Use slices, `&str` and `&Path` when borrowing is sufficient. Keep owned data where it must cross a thread boundary or outlive its caller.
- Prefer `impl Trait` or named generics for static dispatch. Use `dyn Trait` for actual runtime heterogeneity or type erasure, not by default.
- Use `FnOnce`, `FnMut` or `Fn` for injectable behavior. Do not invent a callback or clock trait when a closure or explicit time value suffices.
- Do not introduce custom traits merely to wrap one struct. A new trait needs a concrete behavioral contract and a real implementation or substitution need that existing traits cannot express.
- Use enums for closed alternatives such as navigation targets, pane and popup input destinations, connection phases and launch modes. Avoid string dispatch and boolean-plus-ID pairs internally.
- Parse strings at boundaries, then match on domain types. Prefer `From`, `TryFrom`, `Into` and `TryInto` for conversions.
- Prefer typed request and result structures when shapes are known. Preserve genuinely open-ended protocol envelopes rather than forcing a speculative schema.
- Derive standard traits such as `Default`, `PartialEq` and `Eq` where their semantics are valid. Compare complete values when deduplicating operations.
- Every crate exposes its own root `Error` and `Result<T>` (`pub use error::{Error, Result}`), with errors defined using `thiserror`. Use meaningful typed variants for internal failures and match on variants, never on error text.
- Use `anyhow::Result` and context at application, reporting and test-harness boundaries, not as a replacement for typed internal errors. Keep dependency versions in workspace dependencies.
- Never use `String` or `&str` as an error type, including `Result<(), String>`, worker-channel results and intermediate operation state. Strings are allowed as diagnostic payloads, unchanged wire-protocol fields and final UI display text, not as catch-all internal error wrappers.
- Propagate errors with `?`. Preserve original causes with `#[from]` or `#[source]` and keep structured operation and path context until the display boundary. Do not flatten sources with `to_string()` or `format!()` during propagation. Keep `io::Result` where an actual I/O trait or connector contract requires it, and preserve typed sources when adapting errors.
- Test error variants and source chains as well as user-facing diagnostics. Preserve redaction, bounded remote diagnostics, retry and cancellation classification, and protocol compatibility when changing error handling.
- No `unwrap()` or `expect()` in production. Scope test-only allowances to test code.
- Prefer safe Rust. Do not introduce `unsafe` unless there is no practical safe alternative. Investigate standard-library APIs and maintained safe wrappers first. Any unavoidable exception is tightly scoped and documents why safe alternatives are insufficient and what the safety invariants are.
- Prefer guard clauses and readable iterators. Avoid clones, allocations, helper layers and generic parameters that provide no benefit.
- Comments explain invariants, ownership or non-obvious decisions. They do not narrate assignments.

#### GPUI rules
- Treat app, window and entity contexts as UI-thread execution. A foreground `spawn` future does not make blocking work safe.
- Never perform blocking socket or disk I/O, process waits, sleeps or expensive background computation on the UI thread. Use the existing worker threads or the GPUI background executor, then apply results through the appropriate context update.
- Keep render and input paths thin. Render from prepared state and bounded caches. Do not query the daemon, scan files or launch processes while rendering.
- Preserve continuously drained ordered events and the coalesced latest-state mailbox. Do not replace them with an unbounded UI event queue.
- Route connection and startup failures through the authoritative inbox. Reconnect and detach isolate old inboxes, so delayed events and paint acknowledgements cannot affect the new connection.
- Derive connection text and indicators from `ConnectionStatus`. Old local operation errors must not hide a disconnection reason.
- Queue success is not daemon acknowledgement. Keep names such as `last_queued_options` honest and include cell pixel metrics in resize comparisons.
- Use the same geometry for painting, hit testing and IME placement. Popup input and composition bounds target the popup, not the underlying pane.
- Keep glyph visibility separate from decorations. Spaces and wide-character continuation cells may still need underline or strikethrough painting.
- Preserve menu input isolation, focus behavior, Unicode composition and semantic input routing. Do not synthesize terminal keys for operations with endpoint API methods.
- Activity indicators come from the daemon's snapshot, not from guessed terminal output or a client-local seen ledger. Every client shows the same dot. Do not reintroduce per-client acknowledgement without asking.

#### Protocol and safety
- Vendored bincode field and enum variant order is a compatibility contract. Do not reorder declarations as cleanup. Preserve attribution in `crates/herdr-protocol/NOTICE.md`.
- Keep frame, response and geometry bounds, strict decoding and atomic patch validation. Test malformed inputs and limits, not only valid fixtures.
- Never mix snapshot metadata and surface cells across boot IDs or projection revisions. Preserve future-surface buffering and stale-boot rejection.
- Preserve the single in-flight API request, bounded command and event queues, FIFO ordering, bounded write batches and request correlation.
- Partial frame prefixes and payloads must survive read timeouts. Finish an inbound partial frame before dispatching commands against potentially stale state.
- Cancellation must not flush queued work, replay commands or join a worker on the UI thread. Do not add automatic reconnect or replay as an incidental refactor.
- Crossbeam receiver clones compete for events. They are not broadcast subscribers.
- Treat terminal content and daemon messages as untrusted data. Do not execute terminal escapes, follow graphics file paths or run displayed update commands automatically. The one exception is the daemon's OSC 52 `ServerMessage::Clipboard`: decode it and put only its bounded UTF-8 text on the pasteboard, never arbitrary bytes, files or images.
- The normal app never installs, starts, stops or upgrades a personal daemon. Explicit sockets refer to the binary client socket, not the JSON API socket.
- Never commit secrets, credentials, private terminal output or machine-local configuration.

#### Dependencies and features
- Put dependency versions in root `[workspace.dependencies]`. Member crates inherit with `{ workspace = true }`.
- Use the pinned `rust-toolchain.toml` and GPUI version. Do not copy toolchain, feature or release assumptions from Moltis or Arbor.
- Keep workspace lint inheritance and fix warnings rather than widening allowances.
- Keep `integration-test` opt-in. Gate imports, modules and call sites consistently, and verify both default and all-feature builds.
- Do not introduce Tokio or another runtime to wrap the existing blocking socket worker.
- Avoid unrelated lockfile upgrades. Use `--locked` for reproducible verification.

#### Commits
- For nontrivial work, the commit body explains the problem, the chosen approach and important constraints.
- Before committing, inspect status, diff and recent history. Stage only intended files and check for secrets. Do not bypass signing when it fails.
- Do not amend, force-push, discard changes or clean up other worktrees without explicit authorization.

#### Versioning
- The app's version is `version` under `[workspace.package]` in the root `Cargo.toml`, in semver. The app shows it as `GUI x.y.z` at the right of the status bar and in About, and logs it at startup and in log exports. The Herdr version beside it comes from the connected daemon's welcome. Never hardcode or guess it.
- Bump it in the pull request that makes the change, once per pull request, by the most significant kind of change:
  - A new feature or a breaking change: minor while below 1.0 (0.2.0 -> 0.3.0).
  - A bug fix, performance change, refactor or revert: patch (0.2.0 -> 0.2.1).
  - Documentation, tests, CI, build or tooling only: no bump.
- Bump from the version on `develop`, not from another open pull request. When another bump lands first, merge `origin/develop` and bump again from its version.
- Commit `Cargo.toml` and `Cargo.lock` together. A plain `cargo build` updates the lockfile. Its diff touches only the three workspace crates' versions. Put the bump in the change's own commit, not a separate commit.
- State the bump in the pull request summary, for example `Version: 0.2.0 -> 0.2.1`.
- `HERDR_RELEASE_VERSION` is not a version. When set at build time it marks a signed release build (`RELEASE_BUILD`), which turns on macOS Keychain storage and the agent skill offer. No script in this repository sets it. Do not set it in a local build.

#### Pull requests
These rules add to the pull request template. They do not replace its sections.
- Before creating a pull request, review every included commit and the full diff from `develop`. Include exact validation commands and their results, and the remaining manual or native QA.
- Include screenshots in any pull request that changes something visible: before and after images of the real native window, showing the changed area. Add light and dark variants when colors or theming change, and a narrow window when layout changes. If no desktop was available to capture them, say so instead of leaving the section empty.
- Include a QA checklist in the verification part of the description. List each check as a checkbox. Tick the checks that actually ran (automated tests, gates, native checks you performed) with their result. Leave unticked the manual QA a reviewer still has to do, phrased as concrete steps. Never tick a box for a check that did not run.
- Keep user-facing docs and public API examples synchronized with behavior changes. Do not copy another repository's release, issue-tracker or mandatory-push workflow into this one.
- Finish with the outcome, the tests actually run, known limitations and deferred work. Never claim unrun checks passed.

#### Verification details
- Windows is only cross-checked with `just lint-windows`. Native GUI behavior on Windows is unproven. Keep the `cfg(unix)`/`cfg(windows)` split honest. Say plainly what a platform cannot do instead of stubbing behavior that appears to work.
- A macOS run cannot see a Linux-only gate, and neither can the Windows cross-check. Run `just lint-linux`, `just lint-windows` and `just ci` for a change that touches a `cfg` gate.
- Do not claim a code test run that did not happen.
- Add deterministic tests for invariants: ordering, cancellation, fragmentation, revision fencing, error paths, geometry and cleanup. Prefer explicit coordination and bounded waits over timing guesses.
- Test pure session, CLI and geometry logic without a socket or window where possible. Exercise public behavior with mock peers and headless GPUI tests where integration matters.
- Keep test bodies out of production files. Production files only declare `#[cfg(test)] mod tests;`. Put tests for a new feature in a new topic file under the module's `tests/` directory (for example `keymap/tests/pane_keys.rs`, starting with `use super::*;`). Do not insert them into an existing large test module. Keep shared fixtures in the parent `tests.rs`. Parallel pull requests then edit different files instead of conflicting.
- Fix flaky tests rather than hiding them with retries or new ignores. Existing live and native tests are ignored because they require explicit external resources.
- For visual changes, verify the actual native UI when a desktop is available, including narrow layouts, long labels, focus, popup and IME behavior, and clipping. Headless layout tests do not prove native glyph or OS input correctness.
- Measure interactive performance in release mode (`just run`). Preserve bounded glyph caching and deterministic paint budgets. Native timing budgets are machine-dependent.
- Keep CLI parsing pure and based on OS strings, so socket paths need not be UTF-8. Invalid options and help exit before starting GPUI.

#### Opt-in tests

```sh
just test-live /absolute/path/to/herdr
just test-gui /absolute/path/to/herdr
just test-sidebar
just test-perf
```

- Live tests require an explicitly selected `HERDR_TEST_BINARY`. Never fall back to discovering a user's daemon.
- Reuse the shared sandbox: a private unique short directory, a cleared environment, isolated HOME, XDG, config and socket paths, and an explicit desktop-variable allowlist for GUI children only.
- Kill and wait only for exact children created by the test, before removing their sandbox. Never use `server stop`, process-name matching or process-group kills.
- `HERDR_TEST_TMPDIR`, when set, points to an existing parent short enough for Unix socket paths.
- Native, sidebar and performance tests open windows and need an active desktop. State explicitly when these checks were not run. Do not describe headless tests as native end-to-end verification.

### Exceptions to universal rules
None.
