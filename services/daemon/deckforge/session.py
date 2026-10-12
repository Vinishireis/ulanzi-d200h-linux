"""Graphical-session environment, runtime paths and keyboard-injection backend."""
from __future__ import annotations

import errno
import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any, Mapping

# Variables a launched helper needs to reach the user's desktop. GNOME exports
# them to the systemd user manager, which is used when the daemon was started
# from a TTY, SSH or a service without the graphical environment.
_SESSION_KEYS = ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "WAYLAND_DISPLAY", "DISPLAY",
                 "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY")
_systemd_environment: dict[str, str] | None = None


def runtime_dir(env: Mapping[str, str] | None = None) -> Path:
    """Per-user runtime directory, stable even when XDG_RUNTIME_DIR is unset."""
    env = os.environ if env is None else env
    if env.get("XDG_RUNTIME_DIR"):
        return Path(env["XDG_RUNTIME_DIR"])
    candidate = Path(f"/run/user/{os.getuid()}")
    if candidate.is_dir():
        return candidate
    return Path.home() / ".local/run"


def _systemd_user_environment() -> dict[str, str]:
    global _systemd_environment
    if _systemd_environment is None:
        values: dict[str, str] = {}
        systemctl = shutil.which("systemctl")
        if systemctl:
            try:
                result = subprocess.run([systemctl, "--user", "show-environment"], capture_output=True, text=True,
                                        timeout=2, check=False, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                result = None
            if result is not None and result.returncode == 0:
                for line in result.stdout.splitlines():
                    name, sep, value = line.partition("=")
                    if sep and name in _SESSION_KEYS and not value.startswith("$'"):
                        values[name] = value
        _systemd_environment = values
    return dict(_systemd_environment)


def session_environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Environment for helpers launched on the user's desktop."""
    env = dict(os.environ if base is None else base)
    if not env.get("XDG_SESSION_TYPE") or not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")):
        for name, value in _systemd_user_environment().items():
            if not env.get(name):
                env[name] = value
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = str(runtime_dir(env))
    return env


def session_type(env: Mapping[str, str]) -> str:
    declared = env.get("XDG_SESSION_TYPE", "").lower()
    if declared in {"wayland", "x11"}:
        return declared
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return declared or "unknown"


def ydotool_socket(env: Mapping[str, str]) -> Path:
    """Socket used by the ydotool 1.x client; mirrors the client's own lookup."""
    if env.get("YDOTOOL_SOCKET"):
        return Path(env["YDOTOOL_SOCKET"])
    return runtime_dir(env) / ".ydotool_socket"


def socket_ready(path: Path) -> bool:
    """True when ydotoold is bound to the socket (a stale file is refused)."""
    try:
        if not stat.S_ISSOCK(path.stat().st_mode):
            return False
    except OSError:
        return False
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        probe.connect(str(path))
    except OSError as exc:
        # A stream socket at this path still means a live listener.
        return exc.errno == errno.EPROTOTYPE
    finally:
        probe.close()
    return True


def select_input_backend(env: Mapping[str, str], session_override: str | None = None) -> dict[str, Any]:
    """Pick the tool used to inject shortcuts without sending any key.

    GNOME Wayland rejects wtype's virtual-keyboard protocol, so it uses ydotool
    (kernel uinput through the deckforge-ydotoold user service). Other Wayland
    compositors try wtype first; X11 uses xdotool.
    """
    session = (session_override or session_type(env)).lower()
    desktop = env.get("XDG_CURRENT_DESKTOP", "")
    gnome = "gnome" in desktop.lower()
    result: dict[str, Any] = {"session": session, "desktop": desktop, "backend": None, "path": None,
                              "ready": False, "socket": None, "detail": ""}
    if session == "x11":
        xdotool = shutil.which("xdotool")
        result.update(backend="xdotool", path=xdotool, ready=bool(xdotool),
                      detail="Atalhos X11 via xdotool." if xdotool else "Instale xdotool para usar atalhos na sessão X11.")
        return result
    if session != "wayland":
        result["detail"] = "Atalhos globais exigem uma sessão X11 ou Wayland compatível."
        return result
    ydotool, wtype = shutil.which("ydotool"), shutil.which("wtype")
    sock = ydotool_socket(env)
    ydotool_ready = bool(ydotool) and socket_ready(sock)
    if ydotool:
        result["socket"] = str(sock)
    ydotoold_down = (f"ydotoold não está ativo (socket {sock} ausente ou recusado). "
                     "Inicie com `systemctl --user start deckforge-ydotoold.service` ou rode "
                     "`sudo ./scripts/setup-wayland-input.sh`.")
    if ydotool_ready and (gnome or not wtype):
        result.update(backend="ydotool", path=ydotool, ready=True, detail=f"Atalhos Wayland via ydotool (uinput, socket {sock}).")
    elif gnome and ydotool:
        result.update(backend="ydotool", path=ydotool, detail=ydotoold_down)
    elif wtype:
        note = " O GNOME costuma recusar wtype; configure ydotool com ./scripts/setup-wayland-input.sh." if gnome else ""
        result.update(backend="wtype", path=wtype, ready=True,
                      detail=f"Atalhos Wayland via wtype (depende do protocolo virtual-keyboard do compositor).{note}")
    elif ydotool:
        result.update(backend="ydotool", path=ydotool, detail=ydotoold_down)
    else:
        result["detail"] = "Atalhos no Wayland exigem wtype ou ydotool; no GNOME, use sudo ./scripts/setup-wayland-input.sh."
    return result


def input_backend_status() -> dict[str, Any]:
    status = select_input_backend(session_environment())
    status.pop("path", None)
    return status
