"""D200 two-interface display image upload, isolated from read-only input."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path
from threading import Lock
from typing import Any

from .device import d200_write_target
from .icon_library import IconError, icon_root
from .metrics import SystemMetrics
from .render import display_settings, firmware_draws_values, has_visual, host_rendered, render_tile, render_wide
from .session import runtime_dir
from .store import ProfileStore

ICON_SIZE = (196, 196)
FRAME_SIZE = 1024
MAX_FIRST_PAYLOAD = 1016
MAX_ARCHIVE_SIZE = 196 * 1024
SET_BUTTONS = 0x0001
PARTIALLY_UPDATE_BUTTONS = 0x000D
SET_BRIGHTNESS = 0x000A
WIDE_KEY = "2_3"          # editor name of the wide display (2_3 + 2_4)
WIDE_WIRE_KEY = "3_2"     # firmware manifest key of button index 13
SMALL_WINDOW_MODES = {"stats": 0, "clock": 1, "image": 2}
# Host-mode protocol on vendor HID interface 0. Observed on this unit
# (D200, firmware 5.3.1): a CLOCK frame is answered with DEVICE_INFO JSON.
CLOCK = 0x0006
BUTTON_EVENT = 0x0101
HEARTBEAT = 0x0103
ZIP_ACK = 0x010B
DEVICE_INFO = 0x0303
_WRITE_LOCK = Lock()
_metrics = SystemMetrics()


def _icon_root() -> Path:
    return icon_root()


def _render_icon(config: dict[str, Any], size: tuple[int, int] = ICON_SIZE) -> bytes:
    """PNG of one key (background, icon in its color, optional title)."""
    try:
        return render_tile(config, size)
    except IconError as exc:
        raise ValueError(str(exc)) from exc


def _slot_index(key: str) -> tuple[int, str]:
    if key == WIDE_KEY:
        return 13, WIDE_WIRE_KEY
    try:
        row, col = (int(value) for value in key.split("_", 1))
    except (ValueError, AttributeError):
        raise ValueError("Posição de tecla inválida.") from None
    if row not in range(3) or col not in range(5) or (row == 2 and col > 2):
        raise ValueError("A posição selecionada não é uma tecla configurável.")
    return row * 5 + col, f"{col}_{row}"


def _icon_name(wire_key: str, png: bytes) -> str:
    """Archive path that changes whenever the image changes.

    The D200 firmware caches images by file name: new pixels sent under a name
    it already holds can leave the previous image (or nothing) on the LCD. The
    wide display is redrawn every few seconds under the same key, so each frame
    needs a name of its own. The D200 reference (ulanzi-linux) names generated
    images by content hash for the same reason.
    """
    extension = "gif" if png[:4] == b"GIF8" else "png"  # the firmware plays animated GIFs itself
    return f"icons/{wire_key}-{hashlib.sha256(png).hexdigest()[:12]}.{extension}"


def _build_archive(entries: dict[str, tuple[dict[str, Any], bytes]]) -> bytes:
    names = {wire_key: _icon_name(wire_key, png) for wire_key, (_, png) in entries.items()}
    manifest: dict[str, Any] = {}
    for wire_key in entries:
        manifest[wire_key] = {"State": 0, "ViewParam": [{"Icon": names[wire_key]}]}

    def assemble(padding: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")))
            dummy = zipfile.ZipInfo("dummy.txt")
            dummy.compress_type = zipfile.ZIP_STORED
            archive.writestr(dummy, b"A" * padding)
            for wire_key, (_, png) in entries.items():
                archive.writestr(names[wire_key], png)
            sentinel = zipfile.ZipInfo("sentinel.txt")
            sentinel.compress_type = zipfile.ZIP_STORED
            archive.writestr(sentinel, b"")
        return buffer.getvalue()

    for padding in range(1025):
        blob = assemble(padding)
        if all(blob[offset:offset + 1] not in (b"\x00", b"\x7c") for offset in range(1016, len(blob), FRAME_SIZE)):
            if len(blob) > MAX_ARCHIVE_SIZE:
                raise ValueError(f"O pacote de ícones excede o limite seguro do firmware ({len(blob)} bytes; máximo {MAX_ARCHIVE_SIZE}).")
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                bad_entry = archive.testzip()
                if bad_entry is not None:
                    raise ValueError(f"O pacote ZIP ficou corrompido na entrada {bad_entry}.")
            return blob
    raise RuntimeError("Não foi possível alinhar o arquivo aos limites de quadro aceitos pelo firmware D200.")


def _frame(command: int, payload_length: int, payload: bytes) -> bytes:
    return b"\x7c\x7c" + command.to_bytes(2, "big") + payload_length.to_bytes(4, "little") + payload.ljust(MAX_FIRST_PAYLOAD, b"\0")


def _write_all(fd: int, report: bytes) -> None:
    written = os.write(fd, b"\x00" + report)
    if written != FRAME_SIZE + 1:
        raise OSError(f"Escrita HID incompleta: {written}/{FRAME_SIZE + 1} bytes.")


@contextlib.contextmanager
def _device_write_lock():
    """Serialize uploads across processes: the API and the daemon both sync.

    Interleaved frames from two writers would corrupt the ZIP stream, so the
    thread lock alone is not enough.
    """
    path = runtime_dir() / f"deckforge-{os.getuid()}-hid.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with _WRITE_LOCK:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def small_window_payload(display: dict[str, str] | None = None, metrics: dict[str, int] | None = None,
                         now: float | None = None) -> bytes:
    """SET_SMALL_WINDOW_DATA text `mode|cpu|mem|time|gpu`; it doubles as the host keep-alive.

    STATS=0 shows the CPU/RAM/GPU values sent here, CLOCK=1 shows the time sent
    here, BACKGROUND=2 keeps the host-uploaded image of the wide display. The
    STATS/CLOCK values are drawn over the wide image (firmware_draws_values).
    """
    display = display or display_settings(None)
    if not firmware_draws_values({"mode": "clock", "renderer": "deckforge", **display}):
        # BACKGROUND keeps the image DeckForge drew (custom image or clock).
        return b"2|0|0|00:00:00|0"
    mode = display.get("mode", "clock")
    stamp = time.strftime("%I:%M:%S" if display.get("clock_format") == "12h" else "%H:%M:%S", time.localtime(now))
    if mode == "stats":
        m = metrics or {}
        return f"0|{int(m.get('cpu', 0))}|{int(m.get('mem', 0))}|{stamp}|{int(m.get('gpu', 0))}".encode("ascii")
    return f"1|0|0|{stamp}|0".encode("ascii")


def clock_frame(now: float | None = None, display: dict[str, str] | None = None,
                metrics: dict[str, int] | None = None) -> bytes:
    payload = small_window_payload(display, metrics, now)
    return _frame(CLOCK, len(payload), payload)


def brightness_frame(level: int) -> bytes:
    if not 0 <= int(level) <= 100:
        raise ValueError("Brilho deve estar entre 0 e 100.")
    payload = str(int(level)).encode("ascii")  # the firmware expects ASCII digits
    return _frame(SET_BRIGHTNESS, len(payload), payload)


def _send_frames(path: str, *frames: bytes) -> None:
    with _device_write_lock():
        fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
        try:
            for frame in frames:
                _write_all(fd, frame)
        finally:
            os.close(fd)


def send_clock(path: str, display: dict[str, str] | None = None, metrics: dict[str, int] | None = None) -> None:
    _send_frames(path, clock_frame(display=display, metrics=metrics))


def send_brightness(path: str, level: int) -> None:
    _send_frames(path, brightness_frame(level))


def send_wide_image(path: str, png: bytes) -> int:
    """Replace only the wide display image (manifest 3_2) with an additive update."""
    blob = _build_archive({WIDE_WIRE_KEY: ({}, png)})
    with _device_write_lock():
        return _send(path, blob, PARTIALLY_UPDATE_BUTTONS)


def parse_frame(report: bytes) -> tuple[int, bytes] | None:
    """Split a device-to-host `||` report into (command, payload)."""
    if len(report) < 8 or report[:2] != b"\x7c\x7c":
        return None
    length = int.from_bytes(report[4:8], "little")
    return int.from_bytes(report[2:4], "big"), bytes(report[8:8 + min(length, len(report) - 8)])


def parse_device_info(payload: bytes) -> dict[str, Any]:
    """DEVICE_INFO JSON without the serial number (kept out of logs and the UI)."""
    try:
        info = json.loads(payload.split(b"\0", 1)[0].decode("utf-8", errors="replace"))
    except ValueError:
        return {}
    if not isinstance(info, dict):
        return {}
    return {key: str(value)[:64] for key, value in info.items() if key in {"Dversion", "DeviceType", "HardwareVersion", "error"}}


def _drain(fd: int | None, seconds: float, want: int | None = None) -> bool:
    """Read device reports for a while; True if a frame with command ``want`` arrived."""
    if fd is None:
        return False
    import select
    deadline = time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            break
        try:
            report = os.read(fd, 4096)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            return False
        frame = parse_frame(report)
        if want is not None and frame and frame[0] == want:
            return True
    return False


def _send(path: str, blob: bytes, command: int = SET_BUTTONS) -> int:
    fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        # Every sync is a complete snapshot. PARTIALLY_UPDATE_BUTTONS (0x000D)
        # is additive; it is only used afterwards for the wide display image.
        _write_all(fd, _frame(command, len(blob), blob[:MAX_FIRST_PAYLOAD]))
        for offset in range(MAX_FIRST_PAYLOAD, len(blob), FRAME_SIZE):
            _write_all(fd, blob[offset:offset + FRAME_SIZE].ljust(FRAME_SIZE, b"\0"))
    finally:
        os.close(fd)
    return 1 + max(0, (len(blob) - MAX_FIRST_PAYLOAD + FRAME_SIZE - 1) // FRAME_SIZE)


def sync_icons(store: ProfileStore, key: str | None = None, source: str = "api") -> dict[str, Any]:
    """Send the active profile's icons; records the outcome for the UI.

    Success means every HID frame was written. The firmware sends no
    acknowledgement of what the LCD rendered, so it is never reported as such.
    """
    try:
        result = _sync_icons(store, key)
    except (OSError, RuntimeError, ValueError) as exc:
        store.set_state("lcd_sync", {"ok": False, "message": str(exc), "at": time.time(), "source": source})
        raise
    store.set_state("lcd_sync", {"ok": True, "slots": result["slots"], "frames": result["frames"],
                                 "bytes": result["bytes"], "at": time.time(), "source": source,
                                 "acknowledged": result["acknowledged"], "confirmation": result["confirmation"],
                                 "message": result["message"]})
    return result


def _sync_icons(store: ProfileStore, key: str | None) -> dict[str, Any]:
    path, reason = d200_write_target()
    if not path:
        raise RuntimeError(reason or "Interface de escrita D200 indisponível.")

    buttons = store.buttons()
    # The editor may request a sync for one changed key, but the device must
    # receive the whole active profile. Partial updates are additive: they do
    # not reliably clear stale images after profile switches or USB reconnects.
    if key is not None:
        _slot_index(key)
    entries: dict[str, tuple[dict[str, Any], bytes]] = {}
    slots: list[str] = []
    for button_key, config in sorted(buttons.items()):
        if button_key == WIDE_KEY or not has_visual(config):
            continue
        _, wire_key = _slot_index(button_key)
        entries[wire_key] = (config, _render_icon(config))
        slots.append(button_key)
    wide = buttons.get(WIDE_KEY) or {}
    display = display_settings(wide)
    wide_blob = None
    if host_rendered(display) and (display["mode"] != "image" or has_visual(wide)):
        # Index 13 (manifest "3_2") is a 392x196 image, sent as an additive
        # update after the full snapshot, as the D200 reference does. The
        # daemon refreshes the clock/statistics image while it runs.
        try:
            # Statistics: only the background; the firmware draws the values on top.
            wide_png = render_wide(wide, values=not firmware_draws_values(display))
        except IconError as exc:
            raise ValueError(str(exc)) from exc
        wide_blob = _build_archive({WIDE_WIRE_KEY: (wide, wide_png)})
        slots.append(WIDE_KEY)
    blob = _build_archive(entries)
    # Reference sequence for host mode: clock, drain incoming reports, then the
    # ZIP; the firmware answers with ZIP_ACK once it has received the package.
    # Our read fd gets its own copy of each report, so the daemon still sees them.
    try:
        reader = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        reader = None
    try:
        with _device_write_lock():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
                try:
                    _write_all(fd, clock_frame())
                finally:
                    os.close(fd)
            except OSError:
                pass
            _drain(reader, 0.3)
            frames = _send(path, blob)
            acknowledged = _drain(reader, 2.0, want=ZIP_ACK)
            if wide_blob is not None:
                frames += _send(path, wide_blob, PARTIALLY_UPDATE_BUTTONS)
                acknowledged = _drain(reader, 2.0, want=ZIP_ACK) and acknowledged
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
                try:
                    stats = _metrics.sample() if display["mode"] == "stats" else None
                    _write_all(fd, clock_frame(display=display, metrics=stats))  # apply the display mode now
                finally:
                    os.close(fd)
            except OSError:
                pass
    finally:
        if reader is not None:
            os.close(reader)
    if acknowledged:
        message = ("Firmware confirmou o recebimento do pacote (ACK 0x010B). A renderização não é confirmada pelo "
                   "dispositivo; confira a tela física.")
    else:
        message = ("Perfil completo gravado na interface HID, sem ACK do firmware. O dispositivo não confirma a "
                   "renderização; confira a tela física.")
    # "keys" are the firmware's col_row names; "slots" use the editor's row_col.
    return {"ok": True, "keys": list(entries), "slots": slots, "bytes": len(blob) + len(wide_blob or b""), "frames": frames,
            "display": display["mode"],
            "acknowledged": acknowledged, "confirmation": "firmware-ack" if acknowledged else "hid-write",
            "message": message}
