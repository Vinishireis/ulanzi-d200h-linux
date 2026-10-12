"""Read key events from the D200H keyboard interface for diagnostics."""
from __future__ import annotations

import os
import fcntl
import select
import struct
from pathlib import Path
from typing import Iterator


_INPUT_EVENT = struct.Struct("@llHHi")
_EV_SYN = 0
_EV_KEY = 1
_SYN_DROPPED = 3
_EVIOCGRAB = 0x40044590
_MODIFIERS = {29, 125, 42, 54, 56, 97, 100}
# Firmware macros type a whole token within a second or two. A longer pause
# means the buffered plain text is not part of the same launcher macro.
_TEXT_GAP_SECONDS = 5.0

_KEY_NAMES = {
    1: "KEY_ESC", 12: "KEY_MINUS", 13: "KEY_EQUAL", 14: "KEY_BACKSPACE",
    15: "KEY_TAB", 26: "KEY_LEFTBRACE", 27: "KEY_RIGHTBRACE", 28: "KEY_ENTER",
    29: "KEY_LEFTCTRL", 42: "KEY_LEFTSHIFT", 54: "KEY_RIGHTSHIFT",
    56: "KEY_LEFTALT", 57: "KEY_SPACE", 97: "KEY_RIGHTCTRL",
    99: "KEY_SYSRQ", 100: "KEY_RIGHTALT", 125: "KEY_LEFTMETA",
    39: "KEY_SEMICOLON", 40: "KEY_APOSTROPHE", 41: "KEY_GRAVE",
    43: "KEY_BACKSLASH", 51: "KEY_COMMA", 52: "KEY_DOT", 53: "KEY_SLASH",
    126: "KEY_RIGHTMETA", 127: "KEY_COMPOSE",
}
_LETTER_CODES = {
    16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U",
    23: "I", 24: "O", 25: "P", 30: "A", 31: "S", 32: "D", 33: "F",
    34: "G", 35: "H", 36: "J", 37: "K", 38: "L", 44: "Z", 45: "X",
    46: "C", 47: "V", 48: "B", 49: "N", 50: "M",
}
_DIGIT_CODES = {code: str((code - 1) % 10) for code in range(2, 12)}
_TEXT_BY_CODE = {**_LETTER_CODES, **_DIGIT_CODES, 52: ".", 57: " "}

WINDOWS_RUN_SLOTS = {
    "calc": "0_0",
    "cmd": "0_1",
    "control": "0_2",
    "devmgmt.msc": "0_3",
    "explorer": "0_4",
    "wordpad": "1_0",
    "notepad": "1_1",
    "perfmon": "1_2",
    "powershell": "1_3",
    "taskmgr": "1_4",
}
# The D200H's Mac key mode opens Spotlight (Command+Space), types these
# firmware tokens, then presses Enter. Their order follows the 5x2 app grid.
MAC_RUN_SLOTS = {
    "safari.app": "0_0",
    "mail.app": "0_1",
    "finder.app": "0_2",
    "app store.app": "0_3",
    "music.app": "0_4",
    "calendar.app": "1_0",
    "notes.app": "1_1",
    "keynote.app": "1_2",
    "numbers.app": "1_3",
    "pages.app": "1_4",
}
SHORTCUT_SLOTS = {(29, 31): "2_0", (29, 44): "2_1", (29, 46): "2_2"}
MAC_SHORTCUT_SLOTS = {(125, 31): "2_0", (125, 44): "2_1", (125, 46): "2_2"}


class KeyboardMacroDecoder:
    """Map observed Windows launch macros and Ctrl/Command bottom shortcuts."""

    def __init__(self):
        self._held: set[int] = set()
        self._command: list[str] | None = None
        self._last_time: float | None = None
        self.last_unmatched_command: str | None = None
        # Human-readable macro behind the last returned slot, for the logs.
        self.last_match: str | None = None

    def reset(self) -> None:
        """Forget partial state, e.g. after the kernel reported dropped events."""
        self._held.clear()
        self._command = None
        self._last_time = None

    def feed(self, event: dict[str, int | str | float]) -> str | None:
        if event.get("dropped"):
            self.reset()
            return None
        code, value = int(event["code"]), int(event["value"])
        if value == 0:
            self._held.discard(code)
            return None
        if value != 1 or code in self._held:
            return None
        self._held.add(code)
        now = event.get("time")
        if isinstance(now, (int, float)):
            if (self._command is not None and self._last_time is not None
                    and now - self._last_time > _TEXT_GAP_SECONDS):
                self._command = None
            self._last_time = float(now)

        if self._command is not None:
            if code == 28:
                command = "".join(self._command).lower()
                self._command = None
                token = next((candidate for candidate in (command, command.removeprefix("r"))
                              if candidate in WINDOWS_RUN_SLOTS or candidate in MAC_RUN_SLOTS), None)
                slot = (WINDOWS_RUN_SLOTS.get(token) or MAC_RUN_SLOTS.get(token)) if token else None
                self.last_unmatched_command = None if slot else command
                self.last_match = f"macro {token}" if slot else None
                return slot
            if code == 1:
                self._command = None
                return None
            # A modifier press starts a new shortcut/macro, not text for the
            # pending Super+R launch sequence.
            if code in {29, 125}:
                self._command = None
            else:
                char = _TEXT_BY_CODE.get(code)
                if char and len(self._command) < 64:
                    self._command.append(char)
                return None

        if code in {19, 57} and 125 in self._held:
            self._command = []
            return None
        # Some D200 firmware revisions deliver their launcher macro as plain
        # text (for example "rcalc") instead of a Super+R key sequence. The
        # keyboard interface belongs to the deck, so buffer only this device's
        # text and resolve it on Enter against the firmware token allowlist.
        if code not in _MODIFIERS and not self._held.intersection(_MODIFIERS):
            char = _TEXT_BY_CODE.get(code)
            if char and len(self._command or ()) < 64:
                if self._command is None:
                    self._command = []
                self._command.append(char)
        slot = None
        if 29 in self._held:
            slot = SHORTCUT_SLOTS.get((29, code))
            label = "Ctrl"
        elif 125 in self._held:
            slot = MAC_SHORTCUT_SLOTS.get((125, code))
            label = "Command"
        if slot:
            self.last_match = f"atalho {label}+{_LETTER_CODES.get(code, code)}"
        return slot


def _key_name(code: int) -> str:
    if code in _KEY_NAMES:
        return _KEY_NAMES[code]
    if code in _LETTER_CODES:
        return f"KEY_{_LETTER_CODES[code]}"
    if code in _DIGIT_CODES:
        return f"KEY_{_DIGIT_CODES[code]}"
    if 59 <= code <= 68:
        return f"KEY_F{code - 58}"
    if code == 87:
        return "KEY_F11"
    if code == 88:
        return "KEY_F12"
    return f"KEY_{code}"


def keyboard_nodes() -> list[dict[str, object]]:
    """Find event nodes belonging to interface 1 of the D200H."""
    nodes = []
    for event in Path("/sys/class/input").glob("event*"):
        resolved = event.resolve()
        interface_number = None
        usb_ids = None
        for parent in resolved.parents:
            try:
                if interface_number is None and (parent / "bInterfaceNumber").is_file():
                    interface_number = (parent / "bInterfaceNumber").read_text().strip().lower()
                if (parent / "idVendor").is_file() and (parent / "idProduct").is_file():
                    usb_ids = ((parent / "idVendor").read_text().strip().lower(),
                               (parent / "idProduct").read_text().strip().lower())
                    break
            except OSError:
                continue
        if interface_number != "01" or usb_ids != ("2207", "0019"):
            continue
        path = Path("/dev/input") / event.name
        try:
            readable = os.access(path, os.R_OK)
        except OSError:
            readable = False
        nodes.append({"path": str(path), "readable": readable})
    return sorted(nodes, key=lambda node: node["path"])


def read_key_events(path: str, stop=None, grab: bool = False, on_ready=None) -> Iterator[dict[str, int | str | float]]:
    """Yield key events; optionally keep the D200H from forwarding input to apps.

    ``on_ready`` runs once the node is open (and grabbed), so callers can start
    slow work such as an LCD upload without letting macros leak to the desktop.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    pending = bytearray()
    grabbed = False
    try:
        if grab:
            fcntl.ioctl(fd, _EVIOCGRAB, 1)
            grabbed = True
        if on_ready is not None:
            on_ready()
        while stop is None or not stop():
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            try:
                chunk = os.read(fd, _INPUT_EVENT.size * 32)
            except BlockingIOError:
                continue
            if not chunk:
                return
            pending.extend(chunk)
            while len(pending) >= _INPUT_EVENT.size:
                seconds, micros, event_type, code, value = _INPUT_EVENT.unpack(
                    pending[:_INPUT_EVENT.size]
                )
                del pending[:_INPUT_EVENT.size]
                if event_type == _EV_KEY:
                    yield {"key": _key_name(code), "code": code, "value": value, "time": seconds + micros / 1e6}
                elif event_type == _EV_SYN and code == _SYN_DROPPED:
                    yield {"key": "SYN_DROPPED", "code": -1, "value": -1, "dropped": True}
    finally:
        if grabbed:
            try:
                fcntl.ioctl(fd, _EVIOCGRAB, 0)
            except OSError:
                pass
        os.close(fd)
