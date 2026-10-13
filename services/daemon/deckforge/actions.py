"""Typed action runner. All processes use argv without a shell."""
from __future__ import annotations
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from .session import select_input_backend, session_environment, socket_ready, ydotool_socket
from .store import DATA_HOME

MEDIA_KEYS = {"playpause", "next", "previous", "stop", "volumeup", "volumedown", "mute"}
ALLOWED_KEY_NAMES = {"ctrl", "alt", "shift", "super", "tab", "return", "space", "escape", "backspace", "delete", "home", "end", "up", "down", "left", "right", "print", "pageup", "pagedown", "insert", "menu", "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12", *MEDIA_KEYS}
ACTION_TYPES = {"keys", "app", "command", "text", "multi", "profile", "page", "none"}
_STEP_TYPES = {"keys", "app", "command", "text"}
_ACTION_FIELDS = {"keys": {"keys"}, "app": {"argv"}, "command": {"argv"}, "text": {"text"}, "multi": {"steps"},
                  "profile": {"profile"}, "page": {"page"}, "none": set()}
MAX_TEXT = 1000
MAX_STEPS = 10
_SHELLS = {"sh", "bash", "dash", "zsh", "fish", "csh", "tcsh", "ksh"}
# Output of launched programs goes to a file, never to a pipe: a pipe would
# block or SIGPIPE long-running apps once the daemon stops reading it.
ACTION_LOG = DATA_HOME / "logs" / "actions.log"
_ACTION_LOG_LIMIT = 1_000_000
# How long a launch is observed for an immediate failure (xdg-open, missing
# handler, crash on start). Longer-running programs are reported as started.
LAUNCH_CHECK_SECONDS = 1.5

class ActionError(RuntimeError): pass

_WTYPE_KEY_NAMES = {
    "tab": "Tab", "return": "Return", "escape": "Escape", "backspace": "BackSpace",
    "delete": "Delete", "home": "Home", "end": "End", "up": "Up", "down": "Down",
    "left": "Left", "right": "Right", "print": "Print", "pageup": "Prior", "pagedown": "Next",
    "insert": "Insert", "menu": "Menu",
    "playpause": "XF86AudioPlay", "next": "XF86AudioNext", "previous": "XF86AudioPrev", "stop": "XF86AudioStop",
    "volumeup": "XF86AudioRaiseVolume", "volumedown": "XF86AudioLowerVolume", "mute": "XF86AudioMute",
    **{f"f{number}": f"F{number}" for number in range(1, 13)},
}
# xdotool takes X keysyms; modifiers and letters keep their short names.
_XDOTOOL_KEY_NAMES = {name: keysym for name, keysym in _WTYPE_KEY_NAMES.items()}
_WTYPE_MODIFIER_NAMES = {"super": "logo"}

# Linux evdev key codes used by ydotool. It injects input through uinput,
# which also works on GNOME Wayland where the virtual-keyboard protocol used
# by wtype may be disabled by the compositor.
_EVDEV_KEYCODES = {
    "ctrl": 29, "alt": 56, "shift": 42, "super": 125,
    "tab": 15, "return": 28, "space": 57, "escape": 1,
    "backspace": 14, "delete": 111, "home": 102, "end": 107,
    "up": 103, "down": 108, "left": 105, "right": 106, "print": 99,
    "pageup": 104, "pagedown": 109, "insert": 110, "menu": 127,
    "mute": 113, "volumedown": 114, "volumeup": 115, "next": 163, "playpause": 164, "previous": 165, "stop": 166,
    **{f"f{i}": (58 + i if i <= 10 else 87 + i - 11) for i in range(1, 13)},
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33,
    "g": 34, "h": 35, "i": 23, "j": 36, "k": 37, "l": 38,
    "m": 50, "n": 49, "o": 24, "p": 25, "q": 16, "r": 19,
    "s": 31, "t": 20, "u": 22, "v": 47, "w": 17, "x": 45,
    "y": 21, "z": 44,
    **{str(i): (11 if i == 0 else i + 1) for i in range(10)},
}


def validate_keys(keys: Any) -> list[str]:
    if not isinstance(keys, list) or not 1 <= len(keys) <= 4 or any(
            not isinstance(k, str) or (k.lower() not in ALLOWED_KEY_NAMES and not (len(k) == 1 and k.isascii() and k.isalnum()))
            for k in keys):
        raise ActionError("Combinação de teclas inválida.")
    return keys


def _validate_argv(argv: Any) -> list[str]:
    if not isinstance(argv, list) or not argv or len(argv) > 32 or any(not isinstance(a, str) or not a or len(a) > 2048 or "\0" in a for a in argv):
        raise ActionError("Comando inválido; informe um vetor de argumentos.")
    return argv


def _url_argv(kind: str, argv: list[str]) -> list[str]:
    # Older editor saves could store a bare URL as argv[0]. Open it through
    # the desktop handler instead of asking shutil.which() for a URL path.
    if kind == "app" and argv[0].lower().startswith(("http://", "https://")):
        return ["xdg-open", *argv]
    return argv


def _validate_text(text: Any) -> str:
    if not isinstance(text, str) or not text or len(text) > MAX_TEXT or "\0" in text:
        raise ActionError(f"Informe um texto de 1 a {MAX_TEXT} caracteres.")
    return text


def _validate_steps(steps: Any) -> list[dict[str, Any]]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ActionError(f"Uma multi-ação precisa de 1 a {MAX_STEPS} passos.")
    result = []
    for index, step in enumerate(steps, 1):
        action = step.get("action") if isinstance(step, dict) else None
        delay = step.get("delay_ms", 0) if isinstance(step, dict) else 0
        if not isinstance(action, dict) or action.get("type") not in _STEP_TYPES:
            raise ActionError(f"Passo {index}: use atalho, aplicativo, comando ou texto.")
        if not isinstance(delay, int) or not 0 <= delay <= 10_000:
            raise ActionError(f"Passo {index}: o atraso deve ficar entre 0 e 10000 ms.")
        try:
            clean = normalize_action({"type": action["type"], **{k: action[k] for k in _ACTION_FIELDS[action["type"]] if k in action}})
        except ActionError as exc:
            raise ActionError(f"Passo {index}: {exc}") from exc
        result.append({"action": clean, "delay_ms": delay})
    return result


def normalize_action(config: dict[str, Any]) -> dict[str, Any]:
    """Validate an action for storage and drop fields that belong to other types."""
    kind = config.get("type")
    if kind not in ACTION_TYPES:
        raise ActionError("Tipo de ação não suportado.")
    other_fields = set().union(*_ACTION_FIELDS.values()) - _ACTION_FIELDS[kind]
    result = {key: value for key, value in config.items() if key not in other_fields}
    if kind == "keys":
        validate_keys(result.get("keys"))
    elif kind in ("app", "command"):
        result["argv"] = _url_argv(kind, _validate_argv(result.get("argv")))
    elif kind == "text":
        _validate_text(result.get("text"))
    elif kind == "multi":
        result["steps"] = _validate_steps(result.get("steps"))
    elif kind == "profile":
        target = result.get("profile")
        if not isinstance(target, str) or not target or len(target) > 64:
            raise ActionError("Escolha o perfil de destino (ou próximo/anterior).")
    elif kind == "page":
        target = result.get("page")
        if not isinstance(target, str) or not target or len(target) > 64:
            raise ActionError("Escolha a página de destino (ou voltar, início, próxima, anterior).")
    return result


def _shortcut_command(keys: list[str], session_type: str | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    """Build an argv for the active desktop without invoking a shell."""
    choice = select_input_backend(session_environment() if env is None else env, session_type)
    if not choice["ready"]:
        raise ActionError(choice["detail"])
    if choice["backend"] == "xdotool":
        return [choice["path"], "key", "--clearmodifiers", "+".join(_XDOTOOL_KEY_NAMES.get(k.lower(), k) for k in keys)]
    if choice["backend"] == "ydotool":
        return _ydotool_shortcut_command(keys, choice["path"])
    modifiers = [_WTYPE_MODIFIER_NAMES.get(key.lower(), key.lower()) for key in keys[:-1]]
    key = _WTYPE_KEY_NAMES.get(keys[-1].lower(), keys[-1])
    # wtype exposes explicit modifier press/release options. Keeping argv
    # construction here makes all inputs pass through the same validation.
    return [choice["path"], *[part for modifier in modifiers for part in ("-M", modifier)], "-P", key, "-p", key,
            *[part for modifier in reversed(modifiers) for part in ("-m", modifier)]]


def _ydotool_codes(keys: list[str]) -> list[int]:
    codes = [_EVDEV_KEYCODES.get(key.lower()) for key in keys]
    if any(code is None for code in codes):
        raise ActionError("Esta tecla não tem código Linux compatível com ydotool.")
    return codes  # type: ignore[return-value]


def _ydotool_shortcut_command(keys: list[str], executable: str | None = None) -> list[str]:
    executable = executable or shutil.which("ydotool")
    if not executable:
        raise ActionError("Instale ydotool e ative o serviço DeckForge de entrada Wayland.")
    codes = _ydotool_codes(keys)
    modifiers, target = codes[:-1], codes[-1]
    sequence = [*(f"{code}:1" for code in modifiers), f"{target}:1", f"{target}:0",
                *(f"{code}:0" for code in reversed(modifiers))]
    return [executable, "key", *sequence]


def _release_command(backend: str, executable: str, keys: list[str]) -> list[str] | None:
    """Release every key of a combination; releasing an idle key is harmless."""
    if backend == "ydotool":
        codes = [_EVDEV_KEYCODES[key.lower()] for key in keys if key.lower() in _EVDEV_KEYCODES]
        return [executable, "key", *(f"{code}:0" for code in reversed(codes))] if codes else None
    if backend == "xdotool":
        return [executable, "keyup", *(_XDOTOOL_KEY_NAMES.get(k.lower(), k) for k in reversed(keys))]
    if backend == "wtype":
        modifiers = [_WTYPE_MODIFIER_NAMES.get(key.lower(), key.lower()) for key in keys[:-1]]
        return [executable, *[part for modifier in reversed(modifiers) for part in ("-m", modifier)]] if modifiers else None
    return None


def _output_detail(*streams: bytes | str | None) -> str:
    # ydotool 1.x prints connection errors on stdout, not stderr.
    text = " ".join((s.decode(errors="replace") if isinstance(s, bytes) else s).strip() for s in streams if s)
    return " ".join(text.split())[:300]


def _release_keys(backend: str, executable: str, keys: list[str], env: Mapping[str, str]) -> None:
    command = _release_command(backend, executable, keys)
    if not command:
        return
    try:
        subprocess.run(command, timeout=3, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=dict(env), check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_shortcut(command: list[str], keys: list[str], env: Mapping[str, str]) -> str:
    backend = Path(command[0]).name
    combo = "+".join(keys)
    run_env = dict(env)
    if backend == "ydotool":
        run_env["YDOTOOL_SOCKET"] = str(ydotool_socket(env))
    failure: str | None = None
    try:
        completed = subprocess.run(command, timeout=5, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=run_env, check=False)
        if completed.returncode != 0:
            failure = _output_detail(completed.stdout, completed.stderr) or f"código de saída {completed.returncode}"
    except subprocess.CalledProcessError as exc:
        failure = _output_detail(exc.stdout, exc.stderr) or str(exc)
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    if failure is None:
        return f"Atalho {combo} enviado via {backend}"
    # A timeout or a crash may leave modifiers pressed on the virtual device.
    _release_keys(backend, command[0], keys, run_env)
    if backend == "wtype":
        # GNOME Wayland can install wtype while rejecting its virtual keyboard
        # protocol. If ydotoold is running, retry through kernel uinput instead
        # of treating the installed wtype binary as proof that it works.
        ydotool = shutil.which("ydotool")
        if ydotool and socket_ready(ydotool_socket(env)):
            return _run_shortcut(_ydotool_shortcut_command(keys, ydotool), keys, env)
        if "gnome" in env.get("XDG_CURRENT_DESKTOP", "").lower():
            raise ActionError("Atalho não enviado: este GNOME Wayland recusou wtype. Execute `sudo ./scripts/setup-wayland-input.sh` e reinicie o DeckForge.")
        raise ActionError(f"O compositor recusou o atalho {combo}: {failure}")
    if backend == "ydotool":
        raise ActionError(f"ydotool não conseguiu enviar {combo} (teclas liberadas). Confira /dev/uinput e o serviço deckforge-ydotoold: {failure}")
    raise ActionError(f"{backend} não enviou o atalho {combo}: {failure}")


def _is_shell_command(executable: str, argv: list[str]) -> bool:
    name = os.path.basename(executable).lower()
    args = argv[1:]
    if name == "env":
        # `env [VAR=value] sh -c ...` reaches a shell through a wrapper.
        start = next((i for i, arg in enumerate(args) if not arg.startswith("-") and "=" not in arg), None)
        return start is not None and _is_shell_command(args[start], args[start:])
    if name not in _SHELLS:
        return False
    for arg in args:
        if arg == "--command" or (arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]):
            return True
        if not arg.startswith("-"):
            break
    return False


def _open_action_log():
    try:
        ACTION_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if ACTION_LOG.exists() and ACTION_LOG.stat().st_size > _ACTION_LOG_LIMIT:
            os.replace(ACTION_LOG, ACTION_LOG.with_suffix(".log.1"))
        fd = os.open(ACTION_LOG, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        return os.fdopen(fd, "ab")
    except OSError:
        return None


def _log_tail(offset: int) -> str:
    try:
        with ACTION_LOG.open("rb") as log:
            log.seek(offset)
            return _output_detail(log.read(2000)[-600:])
    except OSError:
        return ""


def _launch(kind: str, argv: list[str], env: Mapping[str, str], check_seconds: float) -> str:
    argv = _url_argv(kind, argv)
    exe = shutil.which(argv[0])
    if not exe: raise ActionError(f"Executável não encontrado: {argv[0]}")
    if _is_shell_command(exe, argv):
        raise ActionError("Execução via shell requer uma ação dedicada; este build aceita apenas argv direto.")
    log = _open_action_log()
    offset = log.tell() if log else 0
    try:
        proc = subprocess.Popen([exe, *argv[1:]], stdin=subprocess.DEVNULL, stdout=log or subprocess.DEVNULL,
                                stderr=log or subprocess.DEVNULL, start_new_session=True, close_fds=True, env=dict(env))
    except OSError as exc:
        raise ActionError(f"Não foi possível iniciar {argv[0]}: {exc}") from exc
    finally:
        if log: log.close()
    name = os.path.basename(argv[0])
    try:
        code = proc.wait(timeout=check_seconds)
    except subprocess.TimeoutExpired:
        return f"{name} iniciado (PID {proc.pid}, em execução)"
    if code != 0:
        detail = _log_tail(offset)
        raise ActionError(f"{name} terminou com código {code}{': ' + detail if detail else ''}")
    if name == "xdg-open" and len(argv) > 1:
        return f"xdg-open entregou {argv[1]} ao aplicativo padrão (código 0)"
    return f"{name} concluído (código 0)"


def _type_text(text: str, env: Mapping[str, str]) -> str:
    """Type text into the focused window; the text goes through stdin, never argv parsing."""
    text = _validate_text(text)
    choice = select_input_backend(env)
    if not choice["ready"]:
        raise ActionError(choice["detail"])
    backend, executable = choice["backend"], choice["path"]
    run_env = dict(env)
    if backend == "ydotool":
        # ydotool maps characters to US-layout key codes; it cannot type accents.
        if any(ord(char) > 126 or (ord(char) < 32 and char not in "\n\t") for char in text):
            raise ActionError("O ydotool digita apenas caracteres ASCII (sem acentos nem emoji).")
        run_env["YDOTOOL_SOCKET"] = str(ydotool_socket(env))
        command = [executable, "type", "-d", "6", "-H", "6", "-f", "-"]
    elif backend == "wtype":
        command = [executable, "-"]
    else:
        command = [executable, "type", "--clearmodifiers", "--file", "-"]
    try:
        completed = subprocess.run(command, input=text.encode("utf-8"), timeout=5 + len(text) * 0.05,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=run_env, check=False)
        failure = None if completed.returncode == 0 else (_output_detail(completed.stdout, completed.stderr) or f"código {completed.returncode}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure = str(exc)
    if failure:
        _release_keys(backend, executable, ["shift", "ctrl", "alt"], run_env)
        raise ActionError(f"{backend} não digitou o texto: {failure}")
    return f"Texto digitado via {backend} ({len(text)} caracteres)"


def _run_multi(steps: list[dict[str, Any]], env: Mapping[str, str], launch_check: float) -> str:
    import time
    done = []
    for index, step in enumerate(_validate_steps(steps), 1):
        if step["delay_ms"]:
            time.sleep(step["delay_ms"] / 1000)
        try:
            done.append(execute_action(step["action"], env=env, launch_check=launch_check))
        except ActionError as exc:
            raise ActionError(f"Passo {index} de {len(steps)} falhou ({len(done)} executados antes): {exc}") from exc
    return f"Multi-ação: {len(done)} passos executados — " + "; ".join(done)


def execute_action(action: dict[str, Any], *, env: Mapping[str, str] | None = None,
                   launch_check: float = LAUNCH_CHECK_SECONDS) -> str:
    """Run one saved action and return what was actually confirmed.

    Raises ActionError for every failure, so callers never log a false success.
    Profile and page switches need the profile store and are handled by daemon.run_action.
    """
    kind = action.get("type")
    env = session_environment() if env is None else env
    if kind == "keys":
        keys = validate_keys(action.get("keys"))
        command = _shortcut_command(keys, env=env)
        return _run_shortcut(command, keys, env)
    if kind in ("app", "command"):
        return _launch(kind, _validate_argv(action.get("argv")), env, launch_check)
    if kind == "text":
        return _type_text(action.get("text"), env)
    if kind == "multi":
        return _run_multi(action.get("steps"), env, launch_check)
    if kind == "none":
        return "Sem ação configurada (somente exibição)"
    raise ActionError("Tipo de ação não suportado.")
