"""Read HID events and dispatch configured host actions."""
from __future__ import annotations
import os
import time
import fcntl
import json
from pathlib import Path
from .actions import execute_action
from .device import capture_profile, discover, parse_button_report
from .keyboard import KeyboardMacroDecoder, keyboard_nodes, read_key_events
from .store import ProfileStore
from .transport import HidTransport


def _lock_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".local/run"))) / f"deckforge-{os.getuid()}.lock"


def daemon_status() -> dict[str, object]:
    """Report whether this user's daemon owns its single-instance lock."""
    path = _lock_path()
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except OSError as exc:
        return {"running": False, "intercept_keyboard": False, "pid": None, "error": str(exc)}
    try:
        try:
            metadata = json.loads(os.read(fd, 4096).decode("utf-8") or "{}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            metadata = {}
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"running": True, "intercept_keyboard": metadata.get("intercept_keyboard"),
                    "mode": metadata.get("mode"), "pid": metadata.get("pid"),
                    "started_at": metadata.get("started_at")}
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return {"running": False, "intercept_keyboard": False, "pid": None}
    finally:
        os.close(fd)

def control_nodes():
    # D200H protocol notes identify interface 0 as vendor HID (control); avoid
    # the separate boot-keyboard interface and ADB interface.
    for dev in discover():
        for interface in dev["interfaces"]:
            if interface["number"].lower() in {"00", "0"} and interface["class"] == "03":
                for hid in interface["hidraw"]:
                    yield hid

def read_events(path: str, stop=None):
    """Open hidraw strictly read-only and yield parsed button events."""
    for report in HidTransport(path).read_reports(stop):
        event = parse_button_report(report)
        if event: yield event

def run_forever(store: ProfileStore, intercept_keyboard: bool | None = None):
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    lock_file = os.fdopen(lock_fd, "r+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Já existe uma instância do daemon DeckForge para este usuário") from exc
    lock_file.seek(0)
    lock_file.truncate()
    json.dump({"pid": os.getpid(), "intercept_keyboard": intercept_keyboard, "mode": "auto" if intercept_keyboard is None else ("keyboard_macros" if intercept_keyboard else "vendor_hid"), "started_at": time.time()}, lock_file)
    lock_file.flush()
    store.log("daemon", "DeckForge daemon iniciado")
    last_pressed: dict[int, float] = {}
    keyboard_problem = None
    active_keyboard_node = None
    active_control_node = None
    unavailable_profile = None

    def dispatch(slot: str):
        store.log("button", f"Botão físico recebido: {slot}")
        action = store.buttons().get(slot)
        if action:
            try: store.log("action", execute_action(action))
            except Exception as exc: store.log("error", str(exc))
        else:
            store.log("error", f"O botão {slot} não tem ação configurada no perfil ativo")

    while True:
        profile = capture_profile()
        if profile["mode"] == "unavailable":
            if profile["reason"] != unavailable_profile:
                store.log("error", f"Entrada física indisponível: {profile['reason']}")
                unavailable_profile = profile["reason"]
            time.sleep(2)
            continue
        unavailable_profile = None
        keyboard_mode = (intercept_keyboard is True or
                         (intercept_keyboard is None and profile["mode"] == "keyboard_macros"))

        if keyboard_mode:
            node = next((n for n in keyboard_nodes() if n["readable"]), None)
            if node is None:
                active_keyboard_node = None
                problem = "Interface de teclado do D200H não acessível; revise udev"
                if problem != keyboard_problem:
                    store.log("error", problem)
                    keyboard_problem = problem
                time.sleep(2)
                continue
            decoder = KeyboardMacroDecoder()
            try:
                if active_keyboard_node != node["path"]:
                    active_keyboard_node = node["path"]
                    if keyboard_problem:
                        store.log("daemon", "Interface de teclado do D200H disponível novamente")
                        keyboard_problem = None
                    store.log("daemon", f"Captura de macros ativa ({profile['profile']}) em {node['path']}")
                    # The LCD may return to its factory page after USB power
                    # loss. Restore only saved icon assets on every connection.
                    try:
                        from .lcd_sync import sync_icons
                        result = sync_icons(store)
                        keys = ", ".join(result["keys"]) or "perfil sem ícones (tela limpa)"
                        store.log("sync", f"Snapshot completo gravado pela HID; renderização sem confirmação do firmware: {keys}")
                    except (OSError, PermissionError, RuntimeError, ValueError) as exc:
                        store.log("error", f"Não foi possível reaplicar perfil ao LCD: {exc}")
                for event in read_key_events(node["path"], grab=True):
                    if event["value"] == 1:
                        store.log("input", f"Tecla do D200 recebida: {event['key']} ({event['code']})")
                    slot = decoder.feed(event)
                    if slot:
                        dispatch(slot)
                    elif decoder.last_unmatched_command is not None:
                        store.log("input", f"Macro do D200 sem mapeamento: {decoder.last_unmatched_command}")
                        decoder.last_unmatched_command = None
            except (OSError, PermissionError) as exc:
                active_keyboard_node = None
                problem = f"Teclado D200H desconectado ou não acessível: {exc}"
                if problem != keyboard_problem:
                    store.log("error", problem)
                    keyboard_problem = problem
                time.sleep(2)
            else:
                active_keyboard_node = None
                time.sleep(1)
            continue

        node = next((n for n in control_nodes() if n["readable"]), None)
        if node is None:
            if active_control_node is not None:
                store.log("error", "Interface HID de controle não acessível para captura de slots")
                active_control_node = None
            time.sleep(2); continue
        try:
            if active_control_node != node["path"]:
                active_control_node = node["path"]
                store.log("daemon", f"Captura de slots ativa ({profile['profile']}) em {node['path']}")
                try:
                    from .lcd_sync import sync_icons
                    result = sync_icons(store)
                    keys = ", ".join(result["keys"]) or "perfil sem ícones (tela limpa)"
                    store.log("sync", f"Snapshot enviado ao LCD: {keys}")
                except (OSError, PermissionError, RuntimeError, ValueError) as exc:
                    store.log("error", f"Não foi possível reaplicar perfil ao LCD: {exc}")
            for event in read_events(node["path"]):
                if not event["pressed"]: continue
                now = time.monotonic()
                if now - last_pressed.get(event["slot"], 0) < .25: continue
                last_pressed[event["slot"]] = now
                dispatch(event["key"])
        except (OSError, PermissionError) as exc:
            active_control_node = None
            store.log("error", f"HID desconectado ou inacessível: {exc}")
            time.sleep(2)

