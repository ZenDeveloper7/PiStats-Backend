from __future__ import annotations

import unittest
from unittest.mock import patch

from pi_backend.collectors import StatsCollector
from pi_backend.config import Settings


def settings() -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8787,
        token="test-token",
        dev_mode=False,
        bind_mode="localhost",
        services=(),
        backup_label=None,
        backup_mountpoint=None,
        wake_mac=None,
        wake_broadcast="192.168.1.255",
        wake_port=9,
    )


class CollectorTests(unittest.TestCase):
    def test_unconfigured_backup_drive_does_not_probe_or_select_disks(self) -> None:
        collector = StatsCollector(settings())
        with patch.object(collector, "_read_lsblk") as read_lsblk:
            result = collector.read_backup_drive()

        read_lsblk.assert_not_called()
        self.assertEqual(
            result,
            {
                "connected": False,
                "mounted": False,
                "label": None,
                "device": None,
                "mountpoint": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
