#!/usr/bin/env bash
# Builds herdr-gpui from this checkout and installs it for the current user,
# under ~/.local: the binary, the desktop entry app launchers list, and the
# icon. Rerun after pulling changes; nothing else ever replaces this copy.
set -euo pipefail
repo="$(cd "$(dirname "$0")/../.." && pwd)"
bin="$HOME/.local/bin"
share="$HOME/.local/share"

cd "$repo"
if command -v mise >/dev/null 2>&1; then
  mise exec -- cargo build --release --locked -p herdr-gpui
else
  cargo build --release --locked -p herdr-gpui
fi

selection=$(python3 -I scripts/build-icon.py linux target/release/herdr-gpui)
icon=${selection%$'\n'*}
icon_path=${selection##*$'\n'}

mkdir -p "$bin" "$share/applications" "$(dirname "$share/${icon_path#share/}")"
install -m 0755 target/release/herdr-gpui "$bin/herdr-gpui"
install -m 0644 "$icon" "$share/${icon_path#share/}"

# Absolute Exec: a launcher's environment may not have ~/.local/bin on PATH.
# StartupWMClass ties the window to this entry in docks and switchers.
sed -e "s|^Exec=herdr-gpui$|Exec=$bin/herdr-gpui|" assets/linux/herdr-gpui.desktop \
  >"$share/applications/herdr-gpui.desktop"
echo "StartupWMClass=so.pen.herdr-gpui" >>"$share/applications/herdr-gpui.desktop"

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$share/applications" || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t "$share/icons/hicolor" || true
echo "installed herdr-gpui to $bin, with its launcher entry and icon"
