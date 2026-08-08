"""Small, dependency-free operational metrics for the CameraApp edge agent.

The Raspberry Pi runs with a constrained memory budget, so the agent avoids an
extra monitoring dependency.  Values are deliberately best-effort: an
unavailable operating-system command must never stop collection or syncing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CpuSnapshot:
    total: int
    idle: int


class SystemMetricsCollector:
    """Calculates host CPU use between consecutive calls."""

    def __init__(self) -> None:
        self._previous_cpu_snapshot: CpuSnapshot | None = None

    def collect(self, disk_path: str | Path = "/") -> dict[str, int | None]:
        cpu_percent, snapshot = _cpu_percent(self._previous_cpu_snapshot)
        self._previous_cpu_snapshot = snapshot
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent(),
            "temperature_celsius": temperature_celsius(),
            "disk_percent": disk_percent(disk_path),
            "swap_percent": swap_percent(),
        }


def memory_percent() -> int | None:
    values = _read_meminfo()
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return None
    return _bounded_percent(100 * (1 - (available / total)))


def swap_percent() -> int | None:
    values = _read_meminfo()
    total = values.get("SwapTotal")
    free = values.get("SwapFree")
    if not total or free is None:
        return None
    return _bounded_percent(100 * (1 - (free / total)))


def disk_percent(path: str | Path = "/") -> int | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    if usage.total <= 0:
        return None
    return _bounded_percent(100 * (usage.used / usage.total))


def temperature_celsius() -> int | None:
    """Read the Raspberry Pi firmware temperature, falling back to sysfs."""

    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
        match = re.search(r"([-+]?[0-9]+(?:\.[0-9]+)?)", result.stdout)
        if result.returncode == 0 and match:
            return round(float(match.group(1)))
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass

    thermal_paths = sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp"))
    for thermal_path in thermal_paths:
        try:
            raw_value = float(thermal_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        return round(raw_value / 1000) if raw_value > 1000 else round(raw_value)
    return None


def _cpu_percent(previous: CpuSnapshot | None) -> tuple[int | None, CpuSnapshot | None]:
    current = _read_cpu_snapshot()
    if current is None or previous is None:
        return None, current

    total_delta = current.total - previous.total
    idle_delta = current.idle - previous.idle
    if total_delta <= 0:
        return None, current
    return _bounded_percent(100 * (1 - (idle_delta / total_delta))), current


def _read_cpu_snapshot() -> CpuSnapshot | None:
    try:
        first_line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        values = [int(value) for value in first_line.split()[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return CpuSnapshot(total=sum(values), idle=idle)


def _read_meminfo() -> dict[str, int]:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    values: dict[str, int] = {}
    for line in lines:
        match = re.match(r"([^:]+):\s*([0-9]+)", line)
        if match:
            values[match.group(1)] = int(match.group(2))
    return values


def _bounded_percent(value: float) -> int:
    return max(0, min(100, round(value)))
