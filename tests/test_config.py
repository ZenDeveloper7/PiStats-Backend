from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pi_backend.config import load_settings


class ConfigTests(unittest.TestCase):
    def test_defaults_do_not_assume_personal_services_or_devices(self) -> None:
        with patch.dict(os.environ, {"PISTATS_TOKEN": "test-token"}, clear=True):
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
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_SERVICES": "samba, pihole",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.services, ("samba", "pihole"))

    def test_token_is_required_outside_local_development(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "PISTATS_TOKEN"):
                load_settings()

    def test_development_mode_cannot_disable_auth_on_network_bind(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_DEV_MODE": "1",
                "PISTATS_BIND_MODE": "custom",
                "PISTATS_HOST": "0.0.0.0",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "localhost"):
                load_settings()

    def test_invalid_wake_configuration_fails_at_startup(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_WAKE_MAC": "not-a-mac",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "PISTATS_WAKE_MAC"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
