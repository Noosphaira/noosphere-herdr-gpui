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
for f in global.yaml repos.yaml settings.yaml teams/app-team.yaml; do
  [ -e "$HOME/.config/herdr-launch/$f" ] || install -m 0644 "$src/config/$f" "$HOME/.config/herdr-launch/$f"
done
# The slice agents run in, and a unit that keeps it present (herdr_netfilter.py).
mkdir -p "$HOME/.config/systemd/user"
install -m 0644 "$src/systemd/herdr-agents.slice" "$src/systemd/herdr-agents-anchor.service" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now herdr-agents-anchor.service
echo "installed to $dest"
