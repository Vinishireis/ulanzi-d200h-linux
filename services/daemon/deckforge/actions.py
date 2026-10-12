"""Typed action runner. All processes use argv without a shell."""
from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

ALLOWED_KEY_NAMES = {"ctrl", "alt", "shift", "super", "tab", "return", "space", "escape", "backspace", "delete", "home", "end", "up", "down", "left", "right", "print", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12"}

class ActionError(RuntimeError): pass

_WTYPE_KEY_NAMES = {
    "tab": "Tab", "return": "Return", "escape": "Escape", "backspace": "BackSpace",
    "delete": "Delete", "home": "Home", "end": "End", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "print": "Print",
    **{f"f{number}": f"F{number}" for number in range(1, 13)},
}
_WTYPE_MODIFIER_NAMES = {"super": "logo"}

# Linux evdev key codes used by ydotool. It injects input through uinput,
# which also works on GNOME Wayland where the virtual-keyboard protocol used
# by wtype may be disabled by the compositor.
_EVDEV_KEYCODES = {
    "ctrl": 29, "alt": 56, "shift": 42, "super": 125,
    "tab": 15, "return": 28, "space": 57, "escape": 1,
    "backspace": 14, "delete": 111, "home": 102, "end": 107,
    "up": 103, "down": 108, "left": 105, "right": 106, "print": 99,
    **{f"f{i}": (58 + i if i <= 10 else 87 + i - 11) for i in range(1, 13)},
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33,
    "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38,
    "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
    "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    **{str(i): (11 if i == 0 else i + 1) for i in range(10)},
}


def _shortcut_command(keys: list[str], session_type: str | None = None) -> list[str]:
    """Build an argv for the active desktop without invoking a shell."""
    session = (session_type if session_type is not None else os.environ.get("XDG_SESSION_TYPE", "")).lower()
    if session == "x11":
        xdotool = shutil.which("xdotool")
        if not xdotool:
            raise ActionError("Instale xdotool para usar atalhos na sessão X11.")
        return [xdotool, "key", "--clearmodifiers", "+".join(keys)]
    if session == "wayland":
        ydotool = shutil.which("ydotool")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if ydotool and "gnome" in desktop:
            return _ydotool_shortcut_command(keys, ydotool)
        wtype = shutil.which("wtype")
        if wtype:
            modifiers = [_WTYPE_MODIFIER_NAMES.get(key.lower(), key.lower()) for key in keys[:-1]]
            key = _WTYPE_KEY_NAMES.get(keys[-1].lower(), keys[-1])
            # wtype exposes explicit modifier press/release options. Keeping argv
            # construction here makes all inputs pass through the same validation.
            return [wtype, *[part for modifier in modifiers for part in ("-M", modifier)], "-P", key, "-p", key,
                    *[part for modifier in reversed(modifiers) for part in ("-m", modifier)]]
        if ydotool:
            return _ydotool_shortcut_command(keys, ydotool)
        raise ActionError("Atalhos no Wayland exigem wtype ou ydotool; no GNOME, use ./scripts/setup-wayland-input.sh.")
    raise ActionError("Atalhos globais exigem uma sessão X11 ou Wayland compatível.")


def _ydotool_shortcut_command(keys: list[str], executable: str | None = None) -> list[str]:
    executable = executable or shutil.which("ydotool")
    if not executable:
        raise ActionError("Instale ydotool e ative o serviço DeckForge de entrada Wayland.")
    codes = [_EVDEV_KEYCODES.get(key.lower()) for key in keys]
    if any(code is None for code in codes):
        raise ActionError("Esta tecla não tem código Linux compatível com ydotool.")
    modifiers, target = codes[:-1], codes[-1]
    sequence = [*(f"{code}:1" for code in modifiers), f"{target}:1", f"{target}:0",
                *(f"{code}:0" for code in reversed(modifiers))]
    return [executable, "key", *sequence]


def _run_shortcut(command: list[str]) -> str:
    try:
        subprocess.run(command, check=True, timeout=5, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        # GNOME Wayland can install wtype while rejecting its virtual keyboard
        # protocol. If ydotool is configured, retry through the kernel uinput
        # device instead of treating the installed wtype binary as proof that
        # the compositor accepts it.
        ydotool = shutil.which("ydotool")
        if Path(command[0]).name == "wtype" and ydotool:
            return _run_shortcut(_ydotool_shortcut_command(_keys_from_wtype_command(command), ydotool))
        if Path(command[0]).name == "wtype" and "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower():
            raise ActionError("Atalho não enviado: este GNOME Wayland recusou wtype. Execute `sudo ./scripts/setup-wayland-input.sh` e reinicie `deckforge serve`.") from exc
        if Path(command[0]).name == "ydotool":
            detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
            raise ActionError(f"ydotool não conseguiu enviar o atalho. Confira /dev/uinput e o serviço deckforge-ydotoold: {detail}") from exc
        detail = exc.stderr.decode(errors="replace").strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        raise ActionError(f"O compositor recusou o atalho{': ' + detail if detail else '.'}") from exc
    if Path(command[0]).name == "ydotool":
        return "Atalho enviado via ydotool"
    return "Atalho enviado"


def _keys_from_wtype_command(command: list[str]) -> list[str]:
    """Recover the validated shortcut from the argv to retry via ydotool."""
    keys: list[str] = []
    index = 1
    while index < len(command):
        if command[index] == "-M" and index + 1 < len(command):
            modifier = command[index + 1]
            keys.append("super" if modifier == "logo" else modifier)
            index += 2
        elif command[index] == "-P" and index + 1 < len(command):
            key = command[index + 1]
            normalized = next((name for name, wtype_name in _WTYPE_KEY_NAMES.items() if wtype_name == key), key)
            keys.append(normalized.lower())
            break
        else:
            index += 1
    return keys

def execute_action(action: dict[str, Any]) -> str:
    kind = action.get("type")
    if kind == "keys":
        keys = action.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 4 or any(not isinstance(k, str) or (k.lower() not in ALLOWED_KEY_NAMES and not (len(k) == 1 and k.isalnum())) for k in keys):
            raise ActionError("Combinação de teclas inválida.")
        command = _shortcut_command(keys)
        return _run_shortcut(command)
    if kind in ("app", "command"):
        argv = action.get("argv")
        if not isinstance(argv, list) or not argv or len(argv) > 32 or any(not isinstance(a, str) or len(a) > 2048 or "\0" in a for a in argv):
            raise ActionError("Comando inválido; informe um vetor de argumentos.")
        # Older editor saves could store a bare URL as argv[0]. Open it through
        # the desktop handler instead of asking shutil.which() for a URL path.
        if kind == "app" and argv[0].lower().startswith(("http://", "https://")):
            argv = ["xdg-open", *argv]
        exe = shutil.which(argv[0])
        if not exe: raise ActionError(f"Executável não encontrado: {argv[0]}")
        if os.path.basename(exe).lower() in {"sh", "bash", "dash", "zsh", "fish", "csh", "tcsh", "ksh"} and any(arg in {"-c", "--command"} for arg in argv[1:3]):
            raise ActionError("Execução via shell requer uma ação dedicada; este build aceita apenas argv direto.")
        subprocess.Popen([exe, *argv[1:]], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        return f"Iniciado: {argv[0]}"
    raise ActionError("Tipo de ação não suportado.")

