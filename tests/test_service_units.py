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


if __name__ == "__main__":
    unittest.main()
