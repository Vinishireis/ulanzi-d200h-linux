"""Live status shared by the editor API and `deckforge status`."""
from __future__ import annotations

import os
from typing import Any

from . import build_id
from .daemon import daemon_status
from .device import capture_profile, d200_write_target, discover, environment
from .keyboard import keyboard_nodes
from .session import input_backend_status
from .store import ProfileStore


def runtime_state(store: ProfileStore, daemon: dict[str, Any]) -> dict[str, Any]:
    """Combine the daemon's last published state with whether it is alive.

    State rows survive the daemon, so a capture marked "active" by a process
    that is gone must not be shown as live.
    """
    state = store.get_state()
    capture = state.get("capture")
    link = state.get("host_link")
    if not daemon.get("running"):
        capture = {"state": "stopped", "message": "Executor de ações parado. Inicie com `deckforge serve`."}
    elif not capture or capture.get("pid") != daemon.get("pid"):
        capture = {"state": "starting", "message": "Daemon iniciado; aguardando o primeiro estado de captura."}
    if not daemon.get("running") or not link or link.get("pid") != daemon.get("pid"):
        link = None
    return {"capture": capture, "host_link": link, "device_info": state.get("device_info"),
            "last_button": state.get("last_button"), "last_action": state.get("last_action")}


def collect_status(store: ProfileStore, events: int = 8) -> dict[str, Any]:
    devices = discover()
    capture = capture_profile(devices)
    keyboards = keyboard_nodes() if devices else []
    accessible = any(h["readable"] for d in devices for i in d["interfaces"] for h in i["hidraw"] if i["number"].lower() == "00")
    write_node, write_reason = d200_write_target(devices)
    daemon = daemon_status()
    if daemon.get("running") and daemon.get("build") not in (None, build_id()):
        daemon["stale"] = True
    lcd_last = store.get_state().get("lcd_sync")
    return {"build": build_id(), "pid": os.getpid(),
            "devices": devices, "connected": bool(devices), "accessible": accessible,
            "keyboard": {"nodes": keyboards, "readable": any(n["readable"] for n in keyboards)},
            "lcd_sync": {"available": bool(write_node), "profile": "D200 2-interface" if write_node else None,
                         "reason": write_reason, "last": lcd_last},
            "profile": store.active_profile(), "session": environment(), "configured_buttons": len(store.buttons()),
            "input_capture": capture, "input_backend": input_backend_status(), "daemon": daemon,
            "runtime": runtime_state(store, daemon), "settings": store.settings(), "events": store.recent_events(events)}


def _systemd_unit(name: str) -> str:
    import shutil
    import subprocess
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "indisponível"
    try:
        result = subprocess.run([systemctl, "--user", "is-active", name], capture_output=True, text=True,
                                timeout=2, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return "desconhecido"
    return result.stdout.strip() or "desconhecido"


def integrations(store: ProfileStore) -> list[dict[str, Any]]:
    """Checks of every system piece DeckForge relies on, with real results."""
    import shutil
    from pathlib import Path
    devices = discover()
    capture = capture_profile(devices)
    keyboards = keyboard_nodes() if devices else []
    write_node, write_reason = d200_write_target(devices)
    backend = input_backend_status()
    daemon = daemon_status()
    runtime = runtime_state(store, daemon)
    link = runtime["host_link"] or {}
    ydotoold = _systemd_unit("deckforge-ydotoold.service")
    udev_d200 = Path("/etc/udev/rules.d/99-deckforge-d200h.rules").is_file()
    udev_uinput = Path("/etc/udev/rules.d/70-deckforge-uinput.rules").is_file()

    def item(key, name, ok, detail, fix=None):
        return {"id": key, "name": name, "ok": ok, "detail": detail, "fix": fix}

    return [
        item("usb", "Ulanzi D200H (USB 2207:0019)", bool(devices),
             f"{capture['profile']}: {capture['reason']}" if devices else "Dispositivo não encontrado",
             None if devices else "Conecte o dock e aguarde ~10 s (o firmware reinicia como 18d1:d002 antes)."),
        item("keyboard", "Captura de macros (interface 01)", any(n["readable"] for n in keyboards),
             ", ".join(f"{n['path']} ({'leitura liberada' if n['readable'] else 'sem permissão'})" for n in keyboards) or "Nó evdev não encontrado",
             None if any(n["readable"] for n in keyboards) else "Instale installers/udev/99-deckforge-d200h.rules e reconecte."),
        item("hidraw", "Interface HID de controle (ícones e modo host)", bool(write_node), write_node or write_reason,
             None if write_node else "A regra udev 99-deckforge-d200h.rules libera escrita no hidraw da interface 0."),
        item("daemon", "Executor de ações (daemon)", bool(daemon.get("running")),
             f"PID {daemon.get('pid')} · {runtime['capture'].get('message', '')}" if daemon.get("running") else runtime["capture"]["message"],
             None if daemon.get("running") else "Rode `deckforge restart`."),
        item("host_link", "Modo host do firmware (keep-alive)", link.get("keepalive") == "ok",
             link.get("message") or ("Keep-alive desativado em Automação" if not store.settings()["host_mode"] else "Sem dados do daemon"),
             None),
        item("input", f"Atalhos de teclado ({backend['backend'] or 'sem backend'})", bool(backend["ready"]), backend["detail"],
             None if backend["ready"] else "No GNOME Wayland: sudo ./scripts/setup-wayland-input.sh"),
        item("ydotoold", "Serviço deckforge-ydotoold", ydotoold == "active", f"systemctl --user: {ydotoold}",
             None if ydotoold == "active" else "systemctl --user enable --now deckforge-ydotoold.service"),
        item("xdg-open", "Abrir URLs e arquivos (xdg-open)", bool(shutil.which("xdg-open")),
             shutil.which("xdg-open") or "xdg-open não encontrado", None if shutil.which("xdg-open") else "sudo apt install xdg-utils"),
        item("udev", "Regras udev do DeckForge", udev_d200 and udev_uinput,
             f"99-deckforge-d200h.rules: {'instalada' if udev_d200 else 'ausente'} · 70-deckforge-uinput.rules: {'instalada' if udev_uinput else 'ausente'}",
             None if udev_d200 and udev_uinput else "Revise installers/udev e instale com sudo."),
    ]
