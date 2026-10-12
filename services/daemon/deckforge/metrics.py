"""System usage for the D200 statistics display (CPU, memory, GPU as 0-100)."""
from __future__ import annotations

from pathlib import Path


class SystemMetrics:
    """CPU usage is measured between consecutive samples of /proc/stat."""

    def __init__(self, proc: Path = Path("/proc"), sys_root: Path = Path("/sys")):
        self._proc, self._sys = proc, sys_root
        self._last: tuple[int, int] | None = None

    def cpu(self) -> int:
        try:
            fields = [int(v) for v in (self._proc / "stat").read_text().split("\n", 1)[0].split()[1:]]
        except (OSError, ValueError, IndexError):
            return 0
        idle, total = fields[3] + (fields[4] if len(fields) > 4 else 0), sum(fields[:8])
        last, self._last = self._last, (idle, total)
        if last is None or total <= last[1]:
            return 0
        return max(0, min(100, round(100 * (1 - (idle - last[0]) / (total - last[1])))))

    def memory(self) -> int:
        values = {}
        try:
            for line in (self._proc / "meminfo").read_text().splitlines():
                name, _, rest = line.partition(":")
                values[name] = int(rest.split()[0])
        except (OSError, ValueError, IndexError):
            return 0
        total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
        return max(0, min(100, round(100 * (total - available) / total))) if total else 0

    def gpu(self) -> int:
        """AMD and some Intel drivers expose gpu_busy_percent; others report 0."""
        for path in sorted((self._sys / "class/drm").glob("card*/device/gpu_busy_percent")):
            try:
                return max(0, min(100, int(path.read_text().strip())))
            except (OSError, ValueError):
                continue
        return 0

    def sample(self) -> dict[str, int]:
        return {"cpu": self.cpu(), "mem": self.memory(), "gpu": self.gpu()}
