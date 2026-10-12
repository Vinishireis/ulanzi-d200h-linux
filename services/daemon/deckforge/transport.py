"""Transport abstractions for real and simulated D200H event streams."""
from __future__ import annotations
import os
import select
import threading
from collections import deque
from pathlib import Path
from typing import Iterator, Protocol

class DeviceTransport(Protocol):
    def read_reports(self, stop=None) -> Iterator[bytes]: ...

class HidTransport:
    """Read-only hidraw transport; it never exposes a write operation."""
    def __init__(self, path: str): self.path = path

    def read_reports(self, stop=None):
        fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            while stop is None or not stop():
                ready, _, _ = select.select([fd], [], [], .5)
                if not ready: continue
                try: report = os.read(fd, 4096)
                except BlockingIOError: continue
                if not report: return
                yield report
        finally:
            os.close(fd)

class MockTransport:
    """Thread-safe event source for offline development and repeatable tests."""
    def __init__(self, reports=()):
        self._reports = deque(reports)
        self._condition = threading.Condition()
        self._closed = False

    def feed(self, report: bytes):
        with self._condition:
            if self._closed: raise RuntimeError("Mock transport is closed")
            self._reports.append(bytes(report)); self._condition.notify()

    def close(self):
        with self._condition: self._closed = True; self._condition.notify_all()

    def read_reports(self, stop=None):
        while not (stop and stop()):
            with self._condition:
                if not self._reports and not self._closed: self._condition.wait(.5)
                if self._reports: report = self._reports.popleft()
                elif self._closed: return
                else: continue
            yield report

class RecordingTransport:
    """Read-through wrapper that records the exact input reports to a file."""
    def __init__(self, wrapped: DeviceTransport, path: str | Path):
        self.wrapped, self.path = wrapped, Path(path)

    def read_reports(self, stop=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.chmod(self.path, 0o600)
        with os.fdopen(fd, "ab") as recording:
            for report in self.wrapped.read_reports(stop):
                recording.write(len(report).to_bytes(2, "little")); recording.write(report); recording.flush()
                yield report

