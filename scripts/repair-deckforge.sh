#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$project_dir/.venv/bin/python"
deckforge_bin="$project_dir/.venv/bin/deckforge"
log_dir="${XDG_DATA_HOME:-$HOME/.local/share}/deckforge/logs"

if [[ ! -x "$deckforge_bin" || ! -x "$python_bin" ]]; then
  echo "Ambiente .venv não encontrado. Crie-o e instale as dependências antes de executar." >&2
  exit 2
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "Instale curl para verificar a API local." >&2
  exit 2
fi

mkdir -p "$log_dir"
chmod 700 "$log_dir"

echo "Parando versões antigas do DeckForge..."
# `stop` escala para SIGKILL e só retorna 0 com a porta 8765 e o lock do daemon livres.
if ! "$deckforge_bin" stop; then
  echo "Não foi possível liberar a porta 8765 ou o daemon antigo. Veja a mensagem acima." >&2
  exit 1
fi

echo "Atualizando dependências Python e compilando a interface..."
"$python_bin" -m pip install -e "$project_dir"
npm --prefix "$project_dir/apps/desktop" ci
npm --prefix "$project_dir/apps/desktop" run build

if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] && \
   [[ "${XDG_CURRENT_DESKTOP:-}" == *GNOME* ]] && ! command -v ydotool >/dev/null 2>&1; then
  echo "AVISO: este GNOME Wayland rejeita wtype. Ative o backend global com: sudo $project_dir/scripts/setup-wayland-input.sh" >&2
elif [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]] && ! command -v wtype >/dev/null 2>&1 && ! command -v ydotool >/dev/null 2>&1; then
  echo "AVISO: atalhos globais em Wayland precisam de wtype ou ydotool." >&2
fi

sync_icons() {
  echo "Reenviando todos os ícones gravados no perfil ativo ao LCD..."
  curl -fsS --max-time 30 -X POST -H 'Content-Type: application/json' -d '{}' \
    http://127.0.0.1:8765/api/sync/icons
  echo
}

if systemctl --user is-enabled --quiet deckforge.service 2>/dev/null; then
  # Inicialização automática ativa: o serviço continua rodando depois deste script.
  "$deckforge_bin" restart
  sync_icons
  echo "Reparo concluído. Editor: http://127.0.0.1:8765"
  echo "Logs: deckforge logs -f e journalctl --user -u deckforge.service -f"
  exit 0
fi

echo "Iniciando API e daemon..."
"$deckforge_bin" serve >>"$log_dir/server.log" 2>&1 </dev/null &
server_pid=$!
# Num script, o `serve` em segundo plano não recebe o Ctrl+C do terminal nem
# morre com ele. Encerre-o (e, por consequência, o daemon) ao sair do script.
cleanup() {
  if kill -0 "$server_pid" 2>/dev/null; then
    kill -TERM "$server_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
ready=0
for _ in $(seq 1 40); do
  if curl -fsS --max-time 1 http://127.0.0.1:8765/api/status >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    break
  fi
  sleep .25
done
if [[ "$ready" != 1 ]]; then
  echo "A API não iniciou. Últimas linhas do log:" >&2
  tail -n 40 "$log_dir/server.log" >&2 || true
  exit 1
fi

sync_icons
echo "Reparo concluído. Editor: http://127.0.0.1:8765"
echo "Logs: $log_dir/server.log e $log_dir/daemon.log"
echo "O DeckForge ficará ativo neste terminal; Ctrl+C encerra API e daemon."
wait "$server_pid"
