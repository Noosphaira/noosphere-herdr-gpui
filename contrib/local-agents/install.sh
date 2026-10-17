#!/usr/bin/env bash
# Copies the launcher scripts to ~/.local/share/herdr-launch and links them
# into ~/.local/bin. Copies, not links into the repo: an agent working on this
# repo must not be able to change the sandbox that confines it.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.local/share/herdr-launch"
mkdir -p "$dest" "$HOME/.local/bin" "$HOME/.config/herdr-launch/teams"
install -m 0755 "$src/herdr-sandbox" "$src/herdr-launch" "$src/herdr-team-watch" "$src/herdr-teams" "$src/herdr-repos" "$src/team" "$dest/"
install -m 0644 "$src/herdr_agents.py" "$src/herdr_grants.py" "$src/herdr_filter.py" "$src/herdr_netfilter.py" "$src/herdr_team.py" "$dest/"
ln -sf "$dest/herdr-sandbox" "$HOME/.local/bin/herdr-sandbox"
ln -sf "$dest/herdr-launch" "$HOME/.local/bin/herdr-launch"
ln -sf "$dest/herdr-teams" "$HOME/.local/bin/herdr-teams"
ln -sf "$dest/herdr-repos" "$HOME/.local/bin/herdr-repos"
# Role tools (team-tools/team_tools.py), one link per tool name. Agents get
# them by granting $dest/team-tools read-only and adding its bin to `path` in
# global.yaml.
mkdir -p "$dest/team-tools/bin"
install -m 0755 "$src/team-tools/team_tools.py" "$dest/team-tools/"
for tool in $(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import team_tools; print(*team_tools.TOOLS)' "$src/team-tools"); do
  ln -sfn ../team_tools.py "$dest/team-tools/bin/$tool"
done
# team-report (host only) turns the Allure results the tools leave in a
# worktree's .team/allure/ into an HTML report. Its CLI is pinned and lives
# outside team-tools, so agents never get it.
allure_version=3.20.0
if [ "$(cat "$dest/allure-cli/.version" 2>/dev/null)" != "$allure_version" ]; then
  if command -v npm >/dev/null; then
    npm install --silent --no-fund --no-audit --prefix "$dest/allure-cli" "allure@$allure_version"
    echo "$allure_version" > "$dest/allure-cli/.version"
  else
    echo "npm not found: install Node, then rerun for team-report" >&2
  fi
fi
ln -sf "$dest/team-tools/bin/team-report" "$HOME/.local/bin/team-report"
for f in global.yaml repos.yaml settings.yaml teams/app-team.yaml; do
  [ -e "$HOME/.config/herdr-launch/$f" ] || install -m 0644 "$src/config/$f" "$HOME/.config/herdr-launch/$f"
done
# The slice agents run in, and a unit that keeps it present (herdr_netfilter.py).
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$src/systemd/herdr-agents.slice" "$src/systemd/herdr-agents-anchor.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now herdr-agents-anchor.service
echo "installed to $dest"
