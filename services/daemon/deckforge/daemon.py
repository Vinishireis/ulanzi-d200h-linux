"""Read D200H input and dispatch configured host actions."""
from __future__ import annotations
import contextlib
import errno
import os
import queue
from collections import deque
import signal
import threading
import time
import fcntl
import json
from pathlib import Path
from typing import Any, Callable
from . import build_id
from .actions import ActionError, execute_action
from .device import capture_profile, discover, parse_button_report
from .keyboard import KeyboardMacroDecoder, keyboard_nodes, read_key_events
from .session import runtime_dir
from .store import ProfileStore
from .transport import HidTransport

# Interfaces appear one by one while the dock re-enumerates after a replug
# (it first shows up as 18d1:d002 for a few seconds). Short-lived topology or
# permission gaps are expected then and are not reported as errors.
_SETTLE_SECONDS = 4.0
# In host mode the firmware reports a press on HID interface 0; if it also
# types its keyboard macro, both describe the same click. Pair them so the
# action runs once. Separate presses on the same channel are never merged.
_PAIR_WINDOW_SECONDS = 2.0
_KEEPALIVE_SECONDS = 2.0
# While nobody reads HID interface 0 the dock keeps its last report pending and
# delivers it as soon as the daemon opens hidraw again (seen twice on restart:
# an old press executed instantly). Presses arriving right after opening are
# discarded instead of running a stale action.
_STALE_REPORT_SECONDS = 1.0
# The DeckForge-drawn statistics image is refreshed at this interval; the
# clock image only when the minute changes.
_STATS_REFRESH_SECONDS = 4.0
# While the host sleeps the dock gets no keep-alive and may fall back to its
# factory screen without re-enumerating; the profile is sent again on resume.
_RESUME_GAP_SECONDS = 2.0
_RESUME_REASON = "retomada após suspensão"
# Two touches on the wide display within this window are a double tap. While a
# double-tap action exists, a single touch waits this long before running.
_DOUBLE_TAP_SECONDS = 0.4
WIDE_KEY = "2_3"
DEFAULT_DOUBLE_TAP = {"type": "page", "page": "next"}


def double_tap_action(config: dict[str, Any] | None) -> dict[str, Any]:
    """Action for a double tap on the wide display: the next page unless configured."""
    action = (config or {}).get("double_tap")
    return action if isinstance(action, dict) and action.get("type") else DEFAULT_DOUBLE_TAP


def _suspended_seconds() -> float:
    """Total time the system spent suspended since boot.

    CLOCK_BOOTTIME keeps counting during suspend and CLOCK_MONOTONIC does not,
    so their difference grows by the length of each sleep.
    """
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


class _Settings:
    """Settings saved from the editor, re-read every few seconds."""

    def __init__(self, store: ProfileStore, ttl: float = 3.0):
        self.store, self._ttl = store, ttl
        self._values: dict[str, Any] = {}
        self._loaded = 0.0

    def get(self, key: str) -> Any:
        if time.monotonic() - self._loaded > self._ttl:
            try:
                self._values = self.store.settings()
            except Exception:
                pass
            self._loaded = time.monotonic()
        return self._values.get(key)


def _lock_path() -> Path:
    return runtime_dir() / f"deckforge-{os.getuid()}.lock"


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
                    "started_at": metadata.get("started_at"), "build": metadata.get("build"),
                    "parent_pid": metadata.get("parent_pid")}
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


def switch_profile(store: ProfileStore, target: str) -> str:
    """Activate a profile by id, or the next/previous one in the profile list (pages)."""
    profiles = sorted(store.profiles(), key=lambda p: (p["created_at"] or "", p["id"]))
    if not profiles:
        raise ActionError("Nenhum perfil cadastrado.")
    if target in {"next", "previous"}:
        current = next((i for i, p in enumerate(profiles) if p["active"]), 0)
        chosen = profiles[(current + (1 if target == "next" else -1)) % len(profiles)]
    else:
        chosen = next((p for p in profiles if p["id"] == target), None)
        if chosen is None:
            raise ActionError("O perfil de destino não existe mais.")
    store.activate_profile(chosen["id"])
    return f"Perfil ativo: {chosen['name']}"


def switch_page(store: ProfileStore, target: str) -> str:
    """Show another page of the active profile: a page id, or back/home/next/previous."""
    try:
        page = store.navigate_page(target)
    except ValueError as exc:
        raise ActionError(str(exc)) from None
    return f"Página: {page['name']}"


def run_action(store: ProfileStore, slot: str, source: str,
               execute: Callable[[dict[str, Any]], str] = execute_action,
               on_layout_change: Callable[[str], None] | None = None, gesture: str = "tap") -> tuple[bool, str]:
    """Execute the action saved for ``slot`` in the active profile and log the real outcome.

    gesture="double" runs the wide display's double-tap action instead.
    """
    action = store.buttons().get(slot)
    if gesture == "double":
        action = {"title": "toque duplo", **double_tap_action(action)}
    title = (action or {}).get("title") or (action or {}).get("type") or "sem título"
    if (not action and slot == "2_3") or (action and action.get("type") == "none"):
        # Display-only key (the wide display by default): a touch is not a failure.
        message = f"{slot}: toque recebido; nenhuma ação configurada (somente exibição)"
        store.log("input", message)
        return True, message
    if not action:
        ok, message = False, f"O botão {slot} não tem ação configurada no perfil ativo"
    else:
        try:
            if action.get("type") in {"profile", "page"}:
                # The LCD shows the keys of one profile page; it is redrawn after a switch.
                if action["type"] == "profile":
                    ok, message, reason = True, switch_profile(store, action.get("profile", "")), "troca de perfil"
                else:
                    ok, message, reason = True, switch_page(store, action.get("page", "")), "troca de página"
                if on_layout_change is not None:
                    on_layout_change(reason)
            else:
                ok, message = True, execute(action)
        except Exception as exc:  # every failure must reach the log, never a false success
            ok, message = False, str(exc) or exc.__class__.__name__
    store.log("action" if ok else "error", f"{slot} · {title}: {message}" if action else message)
    store.set_state("last_action", {"slot": slot, "title": title if action else None, "ok": ok,
                                    "message": message, "source": source, "at": time.time()})
    return ok, message


class ActionDispatcher:
    """Run profile actions in press order without blocking the input reader."""

    def __init__(self, store: ProfileStore, execute: Callable[[dict[str, Any]], str] = execute_action,
                 on_layout_change: Callable[[str], None] | None = None, double_tap_seconds: float = _DOUBLE_TAP_SECONDS):
        self.store, self._execute = store, execute
        self.on_layout_change = on_layout_change
        self._double_tap_seconds = double_tap_seconds
        self._pending_tap: threading.Timer | None = None
        self._queue: queue.Queue[tuple[str, str, str] | None] = queue.Queue(maxsize=32)
        self._lock = threading.Lock()
        self._unpaired: dict[tuple[str, str], deque[float]] = {}
        self._thread = threading.Thread(target=self._run, name="deckforge-actions", daemon=True)
        self._thread.start()

    def submit(self, slot: str, source: str, channel: str = "keyboard") -> bool:
        """Queue the action for a physical press. Returns False for a duplicate report."""
        now = time.monotonic()
        with self._lock:
            for (other, other_slot), times in self._unpaired.items():
                while times and now - times[0] > _PAIR_WINDOW_SECONDS:
                    times.popleft()
                if other != channel and other_slot == slot and times:
                    times.popleft()
                    self.store.log("input", f"{slot}: {source} é o mesmo clique já recebido pelo canal {other}; ação não repetida")
                    return False
            self._unpaired.setdefault((channel, slot), deque()).append(now)
        self.store.log("button", f"Botão físico recebido: {slot} ({source})")
        self.store.set_state("last_button", {"slot": slot, "source": source, "channel": channel, "at": time.time()})
        if slot == WIDE_KEY and double_tap_action(self.store.buttons().get(WIDE_KEY))["type"] != "none":
            return self._wide_touch(source)
        return self._enqueue(slot, source)

    def _wide_touch(self, source: str) -> bool:
        """First touch waits for a possible second one; the second makes it a double tap."""
        with self._lock:
            pending, self._pending_tap = self._pending_tap, None
            if pending is None:
                timer = threading.Timer(self._double_tap_seconds, self._single_touch_due, args=(source,))
                timer.daemon = True
                self._pending_tap = timer
                timer.start()
                return True
            pending.cancel()
        return self._enqueue(WIDE_KEY, f"{source}, toque duplo", "double")

    def _single_touch_due(self, source: str) -> None:
        with self._lock:
            if self._pending_tap is not threading.current_thread():
                return  # a second touch already turned it into a double tap
            self._pending_tap = None
        self._enqueue(WIDE_KEY, source)

    def _enqueue(self, slot: str, source: str, gesture: str = "tap") -> bool:
        try:
            self._queue.put_nowait((slot, source, gesture))
        except queue.Full:
            self.store.log("error", f"Fila de ações cheia; o botão {slot} foi descartado")
            return False
        return True

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            slot, source, gesture = item
            run_action(self.store, slot, source, execute=self._execute, on_layout_change=self.on_layout_change, gesture=gesture)

    def close(self, timeout: float = 5) -> None:
        with self._lock:
            if self._pending_tap is not None:
                self._pending_tap.cancel()
                self._pending_tap = None
        self._queue.put(None)
        self._thread.join(timeout)


class LcdSyncWorker:
    """Upload the saved profile to the LCD in the background, one at a time."""

    def __init__(self, store: ProfileStore, sync: Callable[..., dict[str, Any]] | None = None,
                 retry_delays: tuple[float, ...] = (2.0, 5.0),
                 suspended: Callable[[], float] = _suspended_seconds):
        self.store, self._sync, self._retry_delays = store, sync, retry_delays
        self._suspended = suspended
        self._pending = threading.Event()
        self._stop = threading.Event()
        self._reason = ""
        self._thread = threading.Thread(target=self._run, name="deckforge-lcd", daemon=True)
        self._thread.start()

    def request(self, reason: str) -> None:
        self._reason = reason
        self._pending.set()

    def _run(self) -> None:
        asleep = self._suspended()
        while not self._stop.is_set():
            now_asleep = self._suspended()
            if now_asleep - asleep > _RESUME_GAP_SECONDS:
                self.request(_RESUME_REASON)
            asleep = now_asleep
            if not self._pending.wait(0.25):
                continue
            self._pending.clear()
            reason, last_error = self._reason, None
            try:
                enabled = self.store.settings()["host_mode"]
            except Exception:
                enabled = True
            if not enabled:
                self.store.log("sync", f"LCD ({reason}): envio automático desativado em Automação; o D200H fica no modo de macros de fábrica")
                continue
            for delay in (0.0, *self._retry_delays):
                # udev may still be applying hidraw permissions after a replug.
                if delay and self._stop.wait(delay):
                    return
                try:
                    sync = self._sync
                    if sync is None:
                        from .lcd_sync import sync_icons as sync
                    result = sync(self.store, source=f"daemon:{reason}")
                except (OSError, RuntimeError, ValueError) as exc:
                    last_error = exc
                    continue
                slots = ", ".join(result.get("slots", result.get("keys", []))) or "perfil sem ícones (tela limpa)"
                self.store.log("sync", f"LCD ({reason}): {result.get('frames')} quadros gravados na HID para {slots}; "
                                       "o firmware não confirma a renderização")
                break
            else:
                if reason == _RESUME_REASON:
                    # The dock is re-enumerating after the resume; the
                    # reconnection sends the profile again once it is back.
                    self.store.log("sync", f"LCD ({reason}): D200H ainda indisponível ({last_error}); "
                                           "o perfil será reenviado quando ele reconectar")
                else:
                    self.store.log("error", f"Não foi possível reaplicar o perfil ao LCD ({reason}): {last_error}")

    def close(self, timeout: float = 5) -> None:
        self._stop.set()
        self._thread.join(timeout)


class ControlChannel:
    """Vendor HID interface 0 of the two-interface D200 in host mode.

    After the host talks to it (clock or icon upload) the firmware reports key
    presses here as BUTTON frames instead of typing keyboard macros, answers
    with DEVICE_INFO and expects a clock keep-alive about every 2 s.
    """

    def __init__(self, store: ProfileStore, dispatcher: ActionDispatcher, settings: _Settings | None = None,
                 keepalive: Callable[[str, bytes], None] | None = None):
        from .metrics import SystemMetrics
        self.store, self.dispatcher = store, dispatcher
        self.settings = settings or _Settings(store)
        self._keepalive = keepalive
        self._metrics = SystemMetrics()
        self._brightness_sent: int | None = None
        self._display: tuple[float, dict[str, Any]] = (0.0, {})
        self._wide_config: dict[str, Any] = {}
        self._wide_key: str | None = None
        self._enabled = threading.Event()
        self._stop = threading.Event()
        self._last_press: dict[int, float] = {}
        self._held: set[int] = set()
        self._reported: set[str] = set()
        self._link: dict[str, Any] = {}
        self._link_written = 0.0
        self._opened_at = 0.0
        self._thread = threading.Thread(target=self._run, name="deckforge-hid", daemon=True)
        self._thread.start()

    def enable(self, enabled: bool) -> None:
        (self._enabled.set if enabled else self._enabled.clear)()

    def close(self, timeout: float = 3) -> None:
        self._stop.set()
        self._enabled.set()
        self._thread.join(timeout)

    def _once(self, key: str, kind: str, message: str) -> None:
        if key not in self._reported:
            self._reported.add(key)
            self.store.log(kind, message)

    def _update_link(self, force: bool = False, **values: Any) -> None:
        changed = any(self._link.get(k) != v for k, v in values.items() if k not in {"last_sent", "last_heartbeat", "payload"})
        self._link.update(values)
        if force or changed or time.monotonic() - self._link_written > 10:
            self._link_written = time.monotonic()
            self.store.set_state("host_link", {**self._link, "pid": os.getpid()})

    def _send_keepalive(self) -> None:
        if not self.settings.get("host_mode"):
            self._update_link(keepalive="disabled", message="Keep-alive desativado em Automação")
            return
        from .device import d200_write_target
        path, reason = d200_write_target()
        if not path:
            self._update_link(keepalive="unavailable", message=reason)
            self._once(f"keepalive-blocked:{reason}", "error", f"Keep-alive do modo host indisponível: {reason}")
            return
        from .lcd_sync import brightness_frame, clock_frame, small_window_payload, _send_frames
        from .render import display_settings
        if time.monotonic() - self._display[0] > 2:
            try:
                self._wide_config = self.store.buttons().get("2_3") or {}
                self._display = (time.monotonic(), display_settings(self._wide_config))
            except Exception:
                pass
        display = self._display[1] or display_settings(None)
        metrics = self._metrics.sample() if display["mode"] == "stats" else None
        payload = small_window_payload(display, metrics)
        frames = [clock_frame(display=display, metrics=metrics)]
        brightness = self.settings.get("brightness")
        if isinstance(brightness, int) and 0 <= brightness <= 100 and brightness != self._brightness_sent:
            frames.append(brightness_frame(brightness))
        try:
            if self._keepalive is not None:
                self._keepalive(path, payload)
            else:
                _send_frames(path, *frames)
        except OSError as exc:
            self._update_link(keepalive="error", message=str(exc))
            self._once(f"keepalive-error:{exc.errno}", "error", f"Falha ao enviar keep-alive ao D200H: {exc}")
            return
        if len(frames) > 1:
            self._brightness_sent = brightness
            self.store.log("daemon", f"Brilho do LCD enviado: {brightness}% (o firmware não confirma o valor aplicado)")
        self._refresh_wide(path, display, metrics)
        from .render import firmware_draws_values
        labels = {"clock": "relógio", "stats": "estatísticas do sistema", "image": "imagem personalizada"}
        if display["mode"] == "image":
            drawn = "enviada pelo DeckForge"
        elif not firmware_draws_values(display):
            drawn = "desenhado pelo DeckForge"
        else:
            drawn = ("valores do firmware sobre o fundo do DeckForge (sem reenviar imagens)"
                     if display["renderer"] == "deckforge" else "desenhado pelo firmware")
        self._update_link(keepalive="ok", last_sent=time.time(), display=display["mode"], payload=payload.decode("ascii"),
                          message=f"Keep-alive a cada 2 s; área larga: {labels[display['mode']]} {drawn}")

    def _refresh_wide(self, path: str, display: dict[str, Any], metrics: dict[str, int] | None) -> None:
        """Redraw the DeckForge clock image when the minute changes.

        Statistics are never re-uploaded: the firmware draws their values from
        the keep-alive, over the background sent once by sync_icons.
        """
        from .render import firmware_draws_values
        if display["mode"] == "image" or display["renderer"] != "deckforge" or firmware_draws_values(display):
            self._wide_key = None
            return
        import json as _json
        stamp = time.strftime("%Y%m%d%H%M") if display["mode"] == "clock" else str(int(time.time() // _STATS_REFRESH_SECONDS))
        key = f"{display['mode']}:{stamp}:{_json.dumps(self._wide_config, sort_keys=True)}"
        if key == self._wide_key:
            return
        from .render import render_wide
        from .lcd_sync import send_wide_image
        try:
            png = render_wide(self._wide_config, metrics)
            if self._keepalive is None:
                send_wide_image(path, png)
        except (OSError, RuntimeError, ValueError) as exc:
            self._once(f"wide:{exc}", "error", f"Não foi possível atualizar a área larga: {exc}")
            return
        first = self._wide_key is None
        self._wide_key = key
        if first:
            self.store.log("sync", f"Área larga: {'relógio' if display['mode'] == 'clock' else 'estatísticas'} desenhado pelo DeckForge "
                                   "(sem a marca do firmware); atualizado automaticamente")

    def handle(self, report: bytes) -> None:
        from .lcd_sync import BUTTON_EVENT, DEVICE_INFO, HEARTBEAT, ZIP_ACK, parse_device_info, parse_frame
        frame = parse_frame(report)
        if frame is None:
            return
        command, payload = frame
        if command == BUTTON_EVENT:
            event = parse_button_report(report, strict=False)
            if event is None:
                self._once(f"button:{bytes(report[8:12]).hex()}", "input",
                           f"Evento de botão HID com formato desconhecido: {bytes(report[8:12]).hex()}")
                return
            if not event["pressed"]:
                self._held.discard(event["slot"])
                return
            now = time.monotonic()
            if now - self._opened_at < _STALE_REPORT_SECONDS:
                self.store.log("input", f"Evento HID do slot {event['slot']} descartado: chegou ao reabrir a interface "
                                        "(clique pendente de antes da conexão, não executado)")
                return
            # A repeated press report without a release in between is the same
            # touch. After a release, a quick second press is real (double tap).
            since = now - self._last_press.get(event["slot"], 0)
            if since < .05 or (event["slot"] in self._held and since < .25):
                return
            self._held.add(event["slot"])
            self._last_press[event["slot"]] = now
            source = "toque na área larga (slot HID 13)" if event["slot"] == 13 else f"evento HID do slot {event['slot']}"
            self.dispatcher.submit(event["key"], source, channel="hid")
        elif command == DEVICE_INFO:
            info = parse_device_info(payload)
            self.store.set_state("device_info", {**info, "at": time.time()})
            self._once(f"info:{sorted(info.items())}", "daemon",
                       f"D200H em modo host: {info.get('DeviceType', 'D200')} firmware {info.get('Dversion', '?')}, "
                       f"hardware {info.get('HardwareVersion', '?')}")
            self._update_link(force=True, host_mode=True)
        elif command == HEARTBEAT:
            self._update_link(last_heartbeat=time.time(), host_mode=True)
        elif command != ZIP_ACK:
            self._once(f"cmd:{command}", "input", f"Quadro HID 0x{command:04X} recebido do D200H (não tratado)")

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._enabled.wait(0.5) or self._stop.is_set():
                continue
            node = next((n for n in control_nodes() if n["readable"]), None)
            if node is None:
                self._stop.wait(1)
                continue
            next_keepalive = 0.0

            def tick() -> bool:
                nonlocal next_keepalive
                if self._stop.is_set() or not self._enabled.is_set():
                    return True
                if time.monotonic() >= next_keepalive:
                    next_keepalive = time.monotonic() + _KEEPALIVE_SECONDS
                    self._send_keepalive()
                return False

            self._opened_at = time.monotonic()
            try:
                for report in HidTransport(node["path"]).read_reports(tick):
                    self.handle(report)
            except OSError as exc:
                if exc.errno not in {errno.ENODEV, errno.ENOENT, errno.EIO, errno.ENXIO}:
                    self._once(f"read:{exc.errno}", "error", f"Não foi possível ler a interface HID de controle {node['path']}: {exc}")
                self._update_link(force=True, host_mode=False, keepalive="disconnected", message="Interface HID de controle fechada")
            self._brightness_sent = None  # a replugged device needs the brightness again
            self._wide_key = None
            self._stop.wait(1)


def _pause(seconds: float, should_stop: Callable[[], bool]) -> None:
    deadline = time.monotonic() + seconds
    while not should_stop() and (left := deadline - time.monotonic()) > 0:
        time.sleep(min(.25, left))


class _Reporter:
    """Log state transitions once and publish the capture state for the UI."""

    def __init__(self, store: ProfileStore):
        self.store = store
        self._last: tuple[str, str] | None = None
        self._since: dict[str, float] = {}

    def capture(self, state: str, message: str, kind: str = "daemon", **extra: Any) -> None:
        if self._last == (state, message):
            return
        self._last = (state, message)
        self.store.log(kind, message)
        self.store.set_state("capture", {"state": state, "message": message, "pid": os.getpid(),
                                         "at": time.time(), **extra})

    def transient(self, key: str, state: str, message: str, kind: str = "error", **extra: Any) -> None:
        """Report a condition only if it persists beyond the re-enumeration window."""
        first = self._since.setdefault(key, time.monotonic())
        if time.monotonic() - first >= _SETTLE_SECONDS:
            self.capture(state, message, kind, **extra)
        else:
            self.capture("waiting", "D200H detectado; aguardando as interfaces USB estabilizarem", "device")

    def clear(self) -> None:
        self._since.clear()


def _keyboard_session(store: ProfileStore, node: str, profile: dict[str, str], reporter: _Reporter,
                      dispatcher: ActionDispatcher, lcd: LcdSyncWorker, reconnect: bool,
                      should_stop: Callable[[], bool], settings: _Settings | None = None) -> bool:
    """Capture one keyboard node until it disappears. Returns True if it was opened."""
    decoder = KeyboardMacroDecoder()
    opened = False
    settings = settings or _Settings(store)
    env_log_keys = os.environ.get("DECKFORGE_LOG_KEYS") == "1"

    def on_ready():
        nonlocal opened
        opened = True
        verb = "retomada após reconexão" if reconnect else "ativa"
        reporter.capture("active", f"Captura de macros {verb} ({profile['profile']}) em {node}; teclado isolado com EVIOCGRAB",
                         node=node, profile=profile["profile"], mode=profile["mode"])
        # Start the LCD upload only after the grab, so no macro reaches the desktop meanwhile.
        lcd.request("reconexão" if reconnect else "inicialização")

    try:
        with contextlib.closing(read_key_events(node, should_stop, grab=True, on_ready=on_ready)) as events:
            for event in events:
                if event.get("dropped"):
                    store.log("input", "O kernel descartou eventos do teclado do D200H; decoder reiniciado")
                elif event["value"] == 1 and (env_log_keys or settings.get("log_keys")):
                    store.log("input", f"Tecla do D200 recebida: {event['key']} ({event['code']})")
                slot = decoder.feed(event)
                if slot:
                    dispatcher.submit(slot, decoder.last_match or "macro")
                elif decoder.last_unmatched_command is not None:
                    store.log("input", f"Macro do D200 sem mapeamento: {decoder.last_unmatched_command}")
                    decoder.last_unmatched_command = None
    except OSError as exc:
        if exc.errno == errno.EBUSY:
            reporter.capture("error", f"Outro processo já isolou o teclado do D200H ({node}). Feche `deckforge listen --grab` "
                                      "ou outro daemon DeckForge (`deckforge stop`).", "error", node=node)
        elif exc.errno in {errno.ENODEV, errno.ENOENT, errno.EIO, errno.ENXIO}:
            reporter.capture("waiting", f"D200H desconectado ({node}); aguardando reconexão", "device")
        else:
            reporter.capture("error", f"Teclado D200H não acessível em {node}: {exc}", "error", node=node)
    else:
        if not should_stop():
            reporter.capture("waiting", f"A interface {node} foi fechada; procurando o D200H novamente", "device")
    return opened


def _run_loop(store: ProfileStore, intercept_keyboard: bool | None, should_stop: Callable[[], bool],
              dispatcher: ActionDispatcher, lcd: LcdSyncWorker, control: ControlChannel | None = None) -> None:
    reporter = _Reporter(store)
    settings = _Settings(store)
    captured_before = False
    last_pressed: dict[int, float] = {}
    active_control_node = None
    while not should_stop():
        devices = discover()
        profile = capture_profile(devices)
        if control is not None:
            # The two-interface D200 reports presses on HID interface 0 once in
            # host mode, and as keyboard macros otherwise: listen to both.
            control.enable(profile["profile"] == "D200 2-interface")
        if not devices:
            reporter.clear()
            reporter.capture("waiting", "Aguardando conexão do D200H (USB 2207:0019)", "device")
            _pause(1, should_stop)
            continue
        if profile["mode"] == "unavailable":
            reporter.transient("topology", "error", f"Entrada física indisponível: {profile['reason']}")
            _pause(1, should_stop)
            continue
        keyboard_mode = (intercept_keyboard is True or
                         (intercept_keyboard is None and profile["mode"] == "keyboard_macros"))

        if keyboard_mode:
            nodes = keyboard_nodes()
            node = next((n for n in nodes if n["readable"]), None)
            if node is None:
                problem = (f"Interface de teclado do D200H sem permissão de leitura ({nodes[0]['path']}); revise a regra udev"
                           if nodes else "Interface de teclado do D200H não encontrada")
                reporter.transient("keyboard", "error", problem)
                _pause(1, should_stop)
                continue
            reporter.clear()
            if _keyboard_session(store, node["path"], profile, reporter, dispatcher, lcd, captured_before, should_stop, settings):
                captured_before = True
            _pause(1, should_stop)
            continue

        node = next((n for n in control_nodes() if n["readable"]), None)
        if node is None:
            reporter.transient("control", "error", "Interface HID de controle não acessível para captura de slots")
            active_control_node = None
            _pause(1, should_stop)
            continue
        reporter.clear()
        try:
            if active_control_node != node["path"]:
                active_control_node = node["path"]
                reporter.capture("active", f"Captura de slots ativa ({profile['profile']}) em {node['path']}",
                                 node=node["path"], profile=profile["profile"], mode=profile["mode"])
                lcd.request("reconexão" if captured_before else "inicialização")
                captured_before = True
            for event in read_events(node["path"], should_stop):
                if not event["pressed"]: continue
                now = time.monotonic()
                if now - last_pressed.get(event["slot"], 0) < .25: continue
                last_pressed[event["slot"]] = now
                dispatcher.submit(event["key"], f"slot HID {event['slot']}", channel="hid")
        except OSError as exc:
            active_control_node = None
            reporter.capture("waiting", f"HID desconectado ou inacessível: {exc}", "device")
            _pause(1, should_stop)


def run_forever(store: ProfileStore, intercept_keyboard: bool | None = None, parent_pid: int | None = None):
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    lock_file = os.fdopen(lock_fd, "r+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError("Já existe uma instância do daemon DeckForge para este usuário") from exc
    mode = "auto" if intercept_keyboard is None else ("keyboard_macros" if intercept_keyboard else "vendor_hid")
    lock_file.seek(0)
    lock_file.truncate()
    json.dump({"pid": os.getpid(), "intercept_keyboard": intercept_keyboard, "mode": mode, "started_at": time.time(),
               "build": build_id(), "parent_pid": parent_pid}, lock_file)
    lock_file.flush()

    stopping = threading.Event()
    reason: list[str] = []

    def request_stop(signum, _frame):
        reason.append(signal.Signals(signum).name)
        stopping.set()

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)

    def should_stop() -> bool:
        # Started by `deckforge serve`: never outlive it (covers SIGKILL of the
        # parent, where no cleanup code runs there).
        if not stopping.is_set() and parent_pid and os.getppid() != parent_pid:
            reason.append(f"processo pai {parent_pid} encerrado")
            stopping.set()
        return stopping.is_set()

    store.log("daemon", f"DeckForge daemon iniciado (PID {os.getpid()}, modo {mode}, build {build_id()})")
    store.reset_page()  # after a boot or restart the dock opens on the home page
    dispatcher = ActionDispatcher(store)
    lcd = LcdSyncWorker(store)
    dispatcher.on_layout_change = lcd.request
    control = ControlChannel(store, dispatcher)
    try:
        _run_loop(store, intercept_keyboard, should_stop, dispatcher, lcd, control)
    finally:
        control.close(timeout=3)
        lcd.close(timeout=3)
        dispatcher.close(timeout=3)
        store.set_state("capture", {"state": "stopped", "message": "Daemon encerrado", "pid": os.getpid(), "at": time.time()})
        store.log("daemon", f"DeckForge daemon encerrado ({', '.join(reason) or 'fim do loop'})")
        lock_file.close()
