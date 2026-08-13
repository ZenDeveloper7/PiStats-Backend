from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pi_backend.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults_do_not_assume_personal_services_or_devices(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()

        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.bind_mode, "localhost")
        self.assertEqual(settings.services, ())
        self.assertIsNone(settings.backup_label)
        self.assertIsNone(settings.wake_mac)
        self.assertIsNone(settings.media_backup_root)

    def test_services_are_only_loaded_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {"PISTATS_SERVICES": "samba, pihole"},
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.services, ("samba", "pihole"))


if __name__ == "__main__":
    unittest.main()
