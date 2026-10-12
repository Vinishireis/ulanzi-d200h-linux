#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${EUID}" -ne 0 ]]; then
  echo "Este ajuste instala ydotool e uma regra udev restrita ao grupo plugdev. Execute: sudo $0" >&2
  exit 2
fi

target_user="${SUDO_USER:-}"
if [[ -z "$target_user" || "$target_user" == root ]]; then
  echo "Execute este script com sudo a partir da sessão gráfica do usuário DeckForge." >&2
  exit 2
fi
passwd_entry="$(getent passwd "$target_user")"
target_home="$(cut -d: -f6 <<<"$passwd_entry")"
target_uid="$(cut -d: -f3 <<<"$passwd_entry")"
target_group="$(id -gn "$target_user")"
runtime_dir="/run/user/$target_uid"

if ! id -nG "$target_user" | tr ' ' '\n' | grep -qx plugdev; then
  echo "O usuário $target_user precisa pertencer ao grupo plugdev para usar os dispositivos do DeckForge." >&2
  exit 2
fi
if [[ ! -S "$runtime_dir/bus" ]]; then
  echo "A sessão gráfica de $target_user não está ativa. Entre no GNOME e rode novamente." >&2
  exit 2
fi

apt-get install -y ydotool
install -m 0644 "$project_dir/installers/udev/70-deckforge-uinput.rules" \
  /etc/udev/rules.d/70-deckforge-uinput.rules
udevadm control --reload-rules
udevadm trigger --action=change /sys/devices/virtual/misc/uinput
udevadm settle

if ! runuser -u "$target_user" -- test -r /dev/uinput || \
   ! runuser -u "$target_user" -- test -w /dev/uinput; then
  echo "O usuário não recebeu acesso a /dev/uinput. Confira a regra udev e a associação a plugdev." >&2
  ls -l /dev/uinput >&2 || true
  exit 2
fi

user_unit_dir="$target_home/.config/systemd/user"
install -d -o "$target_user" -g "$target_group" -m 0755 "$user_unit_dir"
install -o "$target_user" -g "$target_group" -m 0644 \
  "$project_dir/installers/systemd/deckforge-ydotoold.service" \
  "$user_unit_dir/deckforge-ydotoold.service"

run_user() {
  runuser -u "$target_user" -- env HOME="$target_home" XDG_RUNTIME_DIR="$runtime_dir" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus" "$@"
}
run_user systemctl --user daemon-reload
run_user systemctl --user enable --now deckforge-ydotoold.service
run_user /usr/bin/ydotool key 29:1 29:0

echo "Entrada global Wayland pronta para $target_user; Ctrl foi pressionado e solto como teste." 
