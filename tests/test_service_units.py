from __future__ import annotations

import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE_FILES = (
    REPOSITORY_ROOT / "install-on-pi.sh",
    REPOSITORY_ROOT / "debian" / "pistats-backend.service",
    REPOSITORY_ROOT / "pistats.service.example",
)


class ServiceUnitTests(unittest.TestCase):
    def test_generic_media_paths_are_not_made_read_only(self) -> None:
        for service_file in SERVICE_FILES:
            with self.subTest(service_file=service_file.name):
                contents = service_file.read_text(encoding="utf-8")
                self.assertNotIn("ProtectSystem=full", contents)
                self.assertIn("NoNewPrivileges=true", contents)
                self.assertIn("ProtectKernelTunables=true", contents)

    def test_transaction_state_paths_survive_source_upgrades(self) -> None:
        installer = (REPOSITORY_ROOT / "install-on-pi.sh").read_text(encoding="utf-8")
        self.assertIn("PISTATS_TRANSACTION_DATABASE", installer)
        self.assertIn("PISTATS_ACTUAL_DATA_DIR", installer)
        self.assertIn(
            'for preserved_path in "${TRANSACTION_DATABASE}" "${ACTUAL_DATA_DIR}"',
            installer,
        )

    def test_debian_package_includes_actual_bridge(self) -> None:
        install_manifest = (REPOSITORY_ROOT / "debian" / "install").read_text(
            encoding="utf-8"
        )
        postinst = (REPOSITORY_ROOT / "debian" / "postinst").read_text(
            encoding="utf-8"
        )
        self.assertIn("pi_backend/*.cjs usr/lib/pistats/pi_backend", install_manifest)
        self.assertIn("PISTATS_TRANSACTION_DATABASE=/var/lib/pistats", postinst)
        self.assertIn("PISTATS_ACTUAL_DATA_DIR=/var/lib/pistats", postinst)


if __name__ == "__main__":
    unittest.main()
