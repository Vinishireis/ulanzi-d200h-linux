#!/usr/bin/env bash
# Inicia o DeckForge (editor, API e daemon) junto com a sessão gráfica, sem
# terminal aberto: ícones, macros e ações voltam sozinhos ao ligar o notebook.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ -x "$project_dir/.venv/bin/deckforge" ]]; then
  deckforge_bin="$project_dir/.venv/bin/deckforge"   # checkout de desenvolvimento (pip install -e)
elif [[ -x "$HOME/.local/bin/deckforge" ]]; then
  deckforge_bin="$HOME/.local/bin/deckforge"         # ./scripts/install-user.sh
else
  echo "DeckForge não está instalado: crie o .venv deste projeto (veja o README) ou rode ./scripts/install-user.sh." >&2
  exit 2
fi
# ExecStart do systemd interpreta espaços e % de forma especial.
if [[ ! "$deckforge_bin" =~ ^[A-Za-z0-9._/+-]+$ ]]; then
  echo "O caminho $deckforge_bin não pode ser usado sem escape no systemd; use ./scripts/install-user.sh." >&2
  exit 2
fi
if [[ ! -f "$project_dir/apps/desktop/dist/index.html" && ! -f "${XDG_DATA_HOME:-$HOME/.local/share}/deckforge/ui/index.html" ]]; then
  echo "AVISO: a interface não está compilada; o editor só abre depois de: npm --prefix apps/desktop run build" >&2
fi

mkdir -p "$unit_dir"
sed "s|^ExecStart=%h/.local/bin/deckforge |ExecStart=$deckforge_bin |" \
  "$project_dir/installers/systemd/deckforge.service" >"$unit_dir/deckforge.service.tmp"
chmod 644 "$unit_dir/deckforge.service.tmp"
mv "$unit_dir/deckforge.service.tmp" "$unit_dir/deckforge.service"

# Unidade antiga, que só iniciava o daemon e forçava o modo teclado.
if [[ -f "$unit_dir/deckforge-daemon.service" ]]; then
  systemctl --user disable --now deckforge-daemon.service 2>/dev/null || true
  rm -f "$unit_dir/deckforge-daemon.service"
fi

systemctl --user daemon-reload
systemctl --user enable deckforge.service
# Encerra instâncias abertas em terminais e inicia o serviço com este código.
"$deckforge_bin" restart
echo "Para desativar: systemctl --user disable --now deckforge.service"
