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
        self.assertFalse(settings.actual_budget_configured)

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

    def test_actual_budget_configuration_must_be_complete(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://127.0.0.1:5006",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "configuration is incomplete"):
                load_settings()

    def test_actual_budget_configuration_is_generic_and_explicit(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://127.0.0.1:5006",
                "PISTATS_ACTUAL_PASSWORD": "actual-password",
                "PISTATS_ACTUAL_SYNC_ID": "budget-sync-id",
                "PISTATS_ACTUAL_CURRENCY": "inr",
                "PISTATS_ACTUAL_MAPPINGS_FILE": "/etc/pistats/mappings.json",
                "PISTATS_ACTUAL_BRIDGE_COMMAND": "/usr/bin/node /opt/bridge.cjs",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertTrue(settings.actual_budget_configured)
        self.assertEqual(settings.actual_currency, "INR")
        self.assertEqual(
            settings.actual_bridge_command,
            ("/usr/bin/node", "/opt/bridge.cjs"),
        )

    def test_actual_currency_is_required_with_other_core_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://127.0.0.1:5006",
                "PISTATS_ACTUAL_PASSWORD": "actual-password",
                "PISTATS_ACTUAL_SYNC_ID": "budget-sync-id",
                "PISTATS_ACTUAL_MAPPINGS_FILE": "/etc/pistats/mappings.json",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "PISTATS_ACTUAL_CURRENCY"):
                load_settings()

    def test_actual_server_url_rejects_embedded_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://user:secret@127.0.0.1:5006",
                "PISTATS_ACTUAL_PASSWORD": "actual-password",
                "PISTATS_ACTUAL_SYNC_ID": "budget-sync-id",
                "PISTATS_ACTUAL_CURRENCY": "INR",
                "PISTATS_ACTUAL_MAPPINGS_FILE": "/etc/pistats/mappings.json",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "without credentials"):
                load_settings()

    def test_actual_passwords_preserve_edge_whitespace(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://127.0.0.1:5006",
                "PISTATS_ACTUAL_PASSWORD": "  actual password  ",
                "PISTATS_ACTUAL_SYNC_ID": "budget-sync-id",
                "PISTATS_ACTUAL_CURRENCY": "INR",
                "PISTATS_ACTUAL_MAPPINGS_FILE": "/etc/pistats/mappings.json",
                "PISTATS_ACTUAL_ENCRYPTION_PASSWORD": " encryption password ",
            },
            clear=True,
        ):
            settings = load_settings()

        self.assertEqual(settings.actual_password, "  actual password  ")
        self.assertEqual(
            settings.actual_encryption_password,
            " encryption password ",
        )

    def test_actual_currency_must_be_a_three_letter_code(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PISTATS_TOKEN": "test-token",
                "PISTATS_ACTUAL_SERVER_URL": "http://127.0.0.1:5006",
                "PISTATS_ACTUAL_PASSWORD": "actual-password",
                "PISTATS_ACTUAL_SYNC_ID": "budget-sync-id",
                "PISTATS_ACTUAL_CURRENCY": "rupees",
                "PISTATS_ACTUAL_MAPPINGS_FILE": "/etc/pistats/mappings.json",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "PISTATS_ACTUAL_CURRENCY"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
