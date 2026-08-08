from __future__ import annotations

import unittest
from unittest.mock import patch

from operational_metrics import CpuSnapshot, SystemMetricsCollector, _cpu_percent


class OperationalMetricsTests(unittest.TestCase):
    def test_cpu_percent_requires_two_snapshots(self) -> None:
        with patch(
            "operational_metrics._read_cpu_snapshot",
            return_value=CpuSnapshot(total=1000, idle=300),
        ):
            percent, snapshot = _cpu_percent(None)

        self.assertIsNone(percent)
        self.assertEqual(snapshot, CpuSnapshot(total=1000, idle=300))

    def test_cpu_percent_is_calculated_between_snapshots(self) -> None:
        with patch(
            "operational_metrics._read_cpu_snapshot",
            return_value=CpuSnapshot(total=1200, idle=340),
        ):
            percent, _ = _cpu_percent(CpuSnapshot(total=1000, idle=300))

        self.assertEqual(percent, 80)

    def test_collector_includes_operational_fields(self) -> None:
        collector = SystemMetricsCollector()
        with patch("operational_metrics.memory_percent", return_value=67), patch(
            "operational_metrics.swap_percent", return_value=4
        ), patch("operational_metrics.temperature_celsius", return_value=45), patch(
            "operational_metrics.disk_percent", return_value=12
        ), patch(
            "operational_metrics._read_cpu_snapshot",
            return_value=CpuSnapshot(total=1000, idle=300),
        ):
            metrics = collector.collect()

        self.assertEqual(
            metrics,
            {
                "cpu_percent": None,
                "memory_percent": 67,
                "temperature_celsius": 45,
                "disk_percent": 12,
                "swap_percent": 4,
            },
        )


if __name__ == "__main__":
    unittest.main()
