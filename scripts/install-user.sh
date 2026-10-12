#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
if ! command -v npm >/dev/null 2>&1; then
  printf 'npm é necessário para preparar a interface local. Instale Node.js/npm e rode novamente.\n' >&2
  exit 1
fi
npm --prefix "$root/apps/desktop" ci
npm --prefix "$root/apps/desktop" run build
python3 -m venv "$HOME/.local/share/deckforge/venv"
"$HOME/.local/share/deckforge/venv/bin/pip" install "$root"
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.local/share/applications" "$HOME/.local/share/deckforge/ui"
cp -a "$root/apps/desktop/dist/." "$HOME/.local/share/deckforge/ui/"
cat > "$HOME/.local/bin/deckforge" <<'EOF'
#!/usr/bin/env sh
exec "$HOME/.local/share/deckforge/venv/bin/deckforge" "$@"
EOF
chmod 755 "$HOME/.local/bin/deckforge"
install -m 644 "$root/installers/systemd/deckforge-daemon.service" "$HOME/.config/systemd/user/deckforge-daemon.service"
install -m 644 "$root/installers/debian/deckforge.desktop" "$HOME/.local/share/applications/deckforge.desktop"
systemctl --user daemon-reload
printf 'Instalado para o usuário. A regra udev não foi instalada. Para revisar: %s\n' "$root/installers/udev/99-deckforge-d200h.rules"
printf 'Ative o daemon quando estiver pronto: systemctl --user enable --now deckforge-daemon.service\n'
