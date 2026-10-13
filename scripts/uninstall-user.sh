#!/usr/bin/env bash
set -euo pipefail
for unit in deckforge.service deckforge-daemon.service; do
  systemctl --user disable --now "$unit" 2>/dev/null || true
done
rm -f "$HOME/.config/systemd/user/deckforge.service" "$HOME/.config/systemd/user/deckforge-daemon.service" "$HOME/.local/share/applications/deckforge.desktop" "$HOME/.local/bin/deckforge"
rm -rf "$HOME/.local/share/deckforge/venv" "$HOME/.local/share/deckforge/ui"
systemctl --user daemon-reload
printf 'Binários removidos. Os dados em ~/.local/share/deckforge foram preservados.\n'
