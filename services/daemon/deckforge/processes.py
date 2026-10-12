"""Find, start and stop this user's DeckForge processes."""
from __future__ import annotations

import ctypes
import json
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

API_HOST, API_PORT = "127.0.0.1", 8765
_ROLES = {"serve", "app", "daemon", "restart"}
_PR_SET_PDEATHSIG = 1


def deckforge_role(argv: list[str]) -> str | None:
    """Return the subcommand of a DeckForge CLI process, or None.

    Matches `deckforge serve`, `python .../bin/deckforge daemon` and
    `python -m deckforge.cli daemon`; arguments of other programs that merely
    mention "deckforge" are ignored.
    """
    if not argv:
        return None
    start = None
    if Path(argv[0]).name == "deckforge":
        start = 1
    elif Path(argv[0]).name.startswith("python"):
        # Interpreter options (e.g. -I, -u) may precede the script or -m.
        index = 1
        while index < len(argv) and argv[index].startswith("-") and argv[index] != "-m":
            index += 1
        if index < len(argv) and Path(argv[index]).name == "deckforge":
            start = index + 1
        elif argv[index:index + 2] == ["-m", "deckforge.cli"]:
            start = index + 2
    if start is None or start >= len(argv):
        return None
    role = argv[start]
    return role if role in _ROLES else None


def find_processes(exclude: set[int] | None = None) -> list[dict[str, Any]]:
    exclude = {os.getpid(), *(exclude or set())}
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in exclude:
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            argv = [part.decode(errors="replace") for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            continue
        role = deckforge_role(argv)
        if role:
            found.append({"pid": int(entry.name), "role": role, "argv": argv})
    return sorted(found, key=lambda item: item["pid"])


def pid_alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def terminate(pids: list[int], grace: float = 5.0, kill_grace: float = 2.0) -> dict[str, list[int]]:
    """SIGTERM, wait, then SIGKILL whatever is left. Never leaves a PID behind silently."""
    def send(targets: list[int], sig: int) -> None:
        for pid in targets:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass

    def wait(targets: list[int], seconds: float) -> list[int]:
        deadline = time.monotonic() + seconds
        while targets and time.monotonic() < deadline:
            targets = [pid for pid in targets if pid_alive(pid)]
            if targets:
                time.sleep(.1)
        return [pid for pid in targets if pid_alive(pid)]

    send(pids, signal.SIGTERM)
    remaining = wait(list(pids), grace)
    killed = list(remaining)
    if remaining:
        send(remaining, signal.SIGKILL)
        remaining = wait(remaining, kill_grace)
    return {"killed": [pid for pid in killed if pid not in remaining], "remaining": remaining}


def api_status(timeout: float = .5) -> dict[str, Any] | None:
    try:
        with urlopen(f"http://{API_HOST}:{API_PORT}/api/status", timeout=timeout) as response:
            if response.status != 200:
                return {}
            return json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, ValueError):
        return None


def port_in_use() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(.3)
    try:
        return probe.connect_ex((API_HOST, API_PORT)) == 0
    finally:
        probe.close()


def die_with_parent() -> None:
    """preexec_fn: deliver SIGTERM to the child if its parent dies (even by SIGKILL)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
    except (OSError, AttributeError):
        pass
