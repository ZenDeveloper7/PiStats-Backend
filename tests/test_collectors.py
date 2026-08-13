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
    def test_discovers_and_normalizes_docker_containers(self) -> None:
        collector = StatsCollector(settings())
        output = "\n".join(
            [
                '{"Names":"samba","State":"running","Status":"Up 2 hours"}',
                '{"Names":"photos","State":"exited","Status":"Exited (0)"}',
            ]
        )
        with patch.object(collector, "_run_command", return_value=output):
            services = collector.list_services()

        self.assertEqual(
            services,
            [
                {"name": "photos", "status": "down", "detail": "Exited (0)"},
                {"name": "samba", "status": "up", "detail": "Up 2 hours"},
            ],
        )

    def test_returns_only_app_selected_services(self) -> None:
        collector = StatsCollector(settings())
        available = [
            {"name": "photos", "status": "up", "detail": "Up"},
            {"name": "samba", "status": "up", "detail": "Up"},
        ]
        with patch.object(collector, "list_services", return_value=available):
            services = collector.read_services(("samba", "missing"))

        self.assertEqual(services, [available[1]])

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
