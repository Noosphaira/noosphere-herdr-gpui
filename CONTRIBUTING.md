# Contributing

Thanks for looking at Herdr GPUI. This file covers the mechanics; the
engineering rules that actually govern changes live in
[AGENTS.md](AGENTS.md) — architecture boundaries, Rust conventions, and
verification expectations. Read it before a non-trivial change. It applies to
human and agent contributors alike.

## Before You Start

- **macOS and experimental Linux builds.** GPUI uses Metal on macOS and
  Vulkan with X11/Wayland on Linux, on x86_64 and ARM64; native Linux desktop
  verification is still pending. The Windows build is experimental and only
  cross-checked with `just lint-windows`.
- **You need Herdr installed.** This repository is a client of the
  daemon from [herdrdev/herdr](https://github.com/herdrdev/herdr). Normal local
  launches may start it if absent; explicit sockets remain attach-only. The GUI
  never installs, stops, or upgrades it. Bugs in the daemon, its session
  model, or its socket handling belong upstream, not here.
- **Open an issue first for anything large.** The README's *Next Milestones*
  section is the roadmap. A PR that lands a milestone differently than planned
  is more likely to stall than a short issue discussing it.

## Setup

Install rustup and, on macOS, the Xcode command-line tools. On Ubuntu 24.04,
run `bash scripts/install-linux-deps.sh` for build libraries and fonts;
see [Linux builds](README.md#linux-builds) for runtime requirements and limitations.
The toolchain is pinned in
`rust-toolchain.toml` and GPUI is pinned to an exact version, so `rustup show`
is enough — do not upgrade either as a side effect of another change. `mise.toml`
mirrors the same Rust version for [mise](https://mise.jdx.dev) users (`mise install`);
change both files together.

```sh
just run          # optimized build; use this for anything interactive
just run-debug    # unoptimized, notably slower with a dense terminal
```

On macOS these launch `target/<profile>/Herdr.app`, so the running app is named
**Herdr** rather than the `herdr-gpui` executable a bare `cargo run` produces.

## Verifying a Change

Run the workspace gates before opening a PR:

```sh
just format
just ci
```

`just ci` runs `cargo fmt --all -- --check`, a `-D warnings` clippy pass over
all targets and features, and the test suite under both default and all
features. Run `just test-build` for linking or packaging changes: it builds the
optimized executable and exercises its CLI without a desktop. The repository
has no hosted CI, so these local runs are the validation.

Some tests are opt-in because they need external resources:

```sh
just test-live /absolute/path/to/herdr   # needs an explicit daemon binary
just test-gui  /absolute/path/to/herdr   # also needs an active desktop
just test-sidebar                        # native glyph regression
just test-perf                           # release-mode scene budget
```

Never point a live test at your personal daemon — they create and clean up
their own isolated one. For visual changes, check the real native window:
narrow layouts, long labels, focus, and clipping. Headless layout tests do not
prove native glyph or input correctness.
Linux X11 and Wayland both need manual desktop coverage; the performance harness
and AppKit click probes remain macOS-only. The GUI test sandbox resolves the
parent Wayland socket without exposing its HOME or runtime directory to daemons.

## Pull Requests

- Follow the branch, commit and pull request rules in [AGENTS.md](AGENTS.md).
  Commit subjects are in the imperative mood, at most 72 characters, followed
  by a blank line and a body that explains what changed and why.
- Run `just hooks` once per clone. It sets `core.hooksPath` to `.githooks`,
  whose hooks check commit messages and run before commits and pushes.
- Branch from `develop` and open pull requests against `develop`. Review your
  own full diff against the base before marking it ready.
- State the validation you actually ran, and say plainly which native or
  manual checks you could not run. Do not describe a headless test as
  end-to-end verification.
- Keep user-facing docs in step with behavior changes.

## License

By contributing, you agree that your contributions are licensed under
Apache-2.0, matching [LICENSE](LICENSE).
