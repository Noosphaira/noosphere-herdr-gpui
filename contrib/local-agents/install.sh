#!/usr/bin/env bash
# Copies the launcher scripts to ~/.local/share/herdr-launch and links them
# into ~/.local/bin. Copies, not links into the repo: an agent working on this
# repo must not be able to change the sandbox that confines it.
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
dest="$HOME/.local/share/herdr-launch"
mkdir -p "$dest" "$HOME/.local/bin" "$HOME/.config/herdr-launch/teams"
install -m 0755 "$src/herdr-sandbox" "$src/herdr-launch" "$src/herdr-team-watch" "$src/herdr-teams" "$src/team" "$dest/"
install -m 0644 "$src/herdr_grants.py" "$src/herdr_filter.py" "$src/herdr_team.py" "$dest/"
ln -sf "$dest/herdr-sandbox" "$HOME/.local/bin/herdr-sandbox"
ln -sf "$dest/herdr-launch" "$HOME/.local/bin/herdr-launch"
ln -sf "$dest/herdr-teams" "$HOME/.local/bin/herdr-teams"
for f in global.yaml repos.yaml teams/app-team.yaml; do
  [ -e "$HOME/.config/herdr-launch/$f" ] || install -m 0644 "$src/config/$f" "$HOME/.config/herdr-launch/$f"
done
echo "installed to $dest"
