from __future__ import annotations

import http.client
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path

from pi_backend.config import Settings
from pi_backend.server import create_handler
from pi_backend.transaction_sync import (
    ActualBridge,
    ActualBridgeError,
    TransactionSyncService,
)


TOKEN = "test-token"
DEVICE_ID = "12345678-1234-5678-9234-567812345678"
EVENT_ID = "sms-v1:" + "a" * 64


def base_settings(**changes: object) -> Settings:
    settings = Settings(
        host="127.0.0.1",
        port=0,
        token=TOKEN,
        dev_mode=False,
        bind_mode="localhost",
        services=(),
        backup_label=None,
        backup_mountpoint=None,
        wake_mac=None,
        wake_broadcast="192.168.1.255",
        wake_port=9,
    )
    return replace(settings, **changes)


class FakeBridge:
    def __init__(self) -> None:
        self.healthy = True
        self.error: str | None = None
        self.checks: list[tuple[str, ...]] = []
        self.imports: list[dict[str, object]] = []
        self.active = 0
        self.maximum_active = 0
        self.delay = 0.0
        self._lock = threading.Lock()

    def check(self, account_ids: tuple[str, ...]) -> None:
        self.checks.append(account_ids)
        if not self.healthy:
            raise ActualBridgeError("network")

    def import_transaction(self, payload: dict[str, object]) -> None:
        with self._lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if self.error:
                raise ActualBridgeError(self.error)
            self.imports.append(payload)
        finally:
            with self._lock:
                self.active -= 1


class RunningServer:
    def __init__(self, settings: Settings, service: TransactionSyncService) -> None:
        self.handler = create_handler(
            settings,
            transaction_sync_service=service,
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> RunningServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.handler.close_services()

    def get(self, path: str) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "GET",
            path,
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def post(
        self,
        payload: dict[str, object],
        *,
        event_id: str = EVENT_ID,
        token: str = TOKEN,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "POST",
            "/api/transactions/sms",
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Idempotency-Key": event_id,
                "X-PiStats-Device-Id": DEVICE_ID,
            },
        )
        response = connection.getresponse()
        response_body = response.read()
        result = response.status, json.loads(response_body) if response_body else {}
        connection.close()
        return result


def event(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": EVENT_ID,
        "received_at_millis": 1_788_249_600_000,
        "transaction_date": "2026-09-01",
        "transaction_time": "14:30:00",
        "amount_minor": -24_550,
        "currency": "INR",
        "direction": "debit",
        "payee": "SWIGGY",
        "account_hint": "1234",
        "bank_reference": "123456789",
        "sender": "VM-HDFCBK",
        "source": "bank_sms",
        "cleared": False,
    }
    payload.update(changes)
    return payload


class TransactionSyncApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.mappings = self.base / "mappings.json"
        self.mappings.write_text(
            json.dumps(
                {
                    "mappings": [
                        {
                            "sender": "VM-HDFCBK",
                            "account_hint": "1234",
                            "actual_account_id": "actual-account-id",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.settings = base_settings(
            actual_server_url="http://127.0.0.1:5006",
            actual_password="actual-password",
            actual_sync_id="budget-sync-id",
            actual_currency="INR",
            actual_mappings_file=str(self.mappings),
            actual_data_dir=str(self.base / "actual-cache"),
            transaction_database=str(self.base / "transactions.sqlite3"),
        )
        self.bridge = FakeBridge()
        self.service = TransactionSyncService(self.settings, self.bridge)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_health_requires_a_working_actual_bridge_and_mapped_account(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.get("/api/health")

        self.assertEqual(status, 200)
        self.assertTrue(payload["features"]["transaction_sync"])
        self.assertTrue(payload["features"]["actual_budget"])
        self.assertEqual(self.bridge.checks, [("actual-account-id",)])

    def test_health_hides_actual_budget_when_runtime_check_fails(self) -> None:
        self.bridge.healthy = False
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.get("/api/health")

        self.assertEqual(status, 200)
        self.assertFalse(payload["features"]["actual_budget"])

    def test_imports_normalized_event_and_returns_conflict_for_duplicate(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event())
            self.assertEqual((status, payload), (201, {"status": "imported"}))
            status, payload = server.post(event())
            self.assertEqual(
                (status, payload),
                (409, {"status": "already_imported"}),
            )

        self.assertEqual(len(self.bridge.imports), 1)
        self.assertEqual(
            self.bridge.imports[0],
            {
                "account_id": "actual-account-id",
                "transaction": {
                    "date": "2026-09-01",
                    "amount": -24_550,
                    "payee_name": "SWIGGY",
                    "notes": "Bank reference: 123456789",
                    "cleared": False,
                    "imported_id": EVENT_ID,
                },
            },
        )
        with sqlite3.connect(self.base / "transactions.sqlite3") as connection:
            row = connection.execute(
                "SELECT state, device_id FROM transaction_imports"
            ).fetchone()
            columns = {
                item[1]
                for item in connection.execute("PRAGMA table_info(transaction_imports)")
            }
        self.assertEqual(row, ("completed", DEVICE_ID))
        self.assertNotIn("body", columns)
        self.assertNotIn("payee", columns)
        self.assertNotIn("amount_minor", columns)

    def test_unknown_mapping_is_rejected_without_calling_actual(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(account_hint="9999"))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "account_mapping_not_found"})
        self.assertEqual(self.bridge.imports, [])

    def test_currency_mismatch_is_rejected_without_calling_actual(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(currency="USD"))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "transaction_currency_mismatch"})
        self.assertEqual(self.bridge.imports, [])
        self.assertFalse((self.base / "transactions.sqlite3").exists())

    def test_rejects_body_field_that_could_contain_the_complete_sms(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(sms_body="complete private message"))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "invalid_transaction_event"})

    def test_rejects_sign_that_disagrees_with_direction(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(amount_minor=24_550))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "amount_direction_mismatch"})

    def test_rejects_out_of_range_receive_timestamp(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(
                event(received_at_millis=253_402_300_800_000)
            )

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "invalid_received_at_millis"})

    def test_rejects_mismatched_idempotency_header(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(), event_id="different")

        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "idempotency_key_mismatch"})

    def test_requires_bearer_auth(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(), token="wrong")

        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

    def test_actual_failure_is_retryable_and_does_not_complete_idempotency(self) -> None:
        self.bridge.error = "network"
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event())
            self.assertEqual(status, 502)
            self.assertEqual(payload, {"error": "actual_import_failed"})
            self.bridge.error = None
            status, payload = server.post(event())

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertEqual(len(self.bridge.imports), 1)

    def test_imports_are_serialized(self) -> None:
        second_event_id = "sms-v1:" + "b" * 64
        self.bridge.delay = 0.05
        results: list[int] = []

        def upload(payload: dict[str, object], event_id: str) -> None:
            with RunningServer(self.settings, self.service) as server:
                status, _ = server.post(payload, event_id=event_id)
                results.append(status)

        first = threading.Thread(target=upload, args=(event(), EVENT_ID))
        second = threading.Thread(
            target=upload,
            args=(event(event_id=second_event_id), second_event_id),
        )
        first.start()
        second.start()
        first.join(timeout=3)
        second.join(timeout=3)

        self.assertEqual(sorted(results), [201, 201])
        self.assertEqual(self.bridge.maximum_active, 1)


class TransactionMappingTests(unittest.TestCase):
    def test_duplicate_normalized_mapping_fails_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mappings = base / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "sender": "vm-hdfcbk",
                                "account_hint": "1234",
                                "actual_account_id": "one",
                            },
                            {
                                "sender": "VM-HDFCBK",
                                "account_hint": "1234",
                                "actual_account_id": "two",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            settings = base_settings(
                actual_server_url="http://127.0.0.1:5006",
                actual_password="actual-password",
                actual_sync_id="budget-sync-id",
                actual_currency="INR",
                actual_mappings_file=str(mappings),
            )

            with self.assertRaisesRegex(ValueError, "duplicate mapping"):
                TransactionSyncService(settings, FakeBridge())


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class ActualBridgeProcessTests(unittest.TestCase):
    def test_bundled_bridge_checks_accounts_and_imports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fake_api = base / "fake-actual-api.cjs"
            fake_api.write_text(
                """
module.exports = {
  init: async () => {
    if (process.env.PISTATS_TOKEN) throw new Error('PiStats token was forwarded');
  },
  downloadBudget: async () => {},
  getAccounts: async () => [{ id: 'account-one' }],
  importTransactions: async (account, transactions, options) => {
    if (account !== 'account-one') throw Object.assign(new Error(), { code: 'account_not_found' });
    if (transactions[0].imported_id !== 'sms-v1:test') return { errors: ['bad'] };
    if (options.defaultCleared !== false || options.reimportDeleted !== false) return { errors: ['bad'] };
    return { errors: [], added: ['transaction-one'], updated: [] };
  },
  sync: async () => {},
  shutdown: async () => {},
};
""",
                encoding="utf-8",
            )
            bridge_script = Path(__file__).parents[1] / "pi_backend" / "actual_bridge.cjs"
            settings = base_settings(
                token="secret-that-must-not-be-forwarded",
                actual_server_url="http://127.0.0.1:5006",
                actual_password="actual-password",
                actual_sync_id="budget-sync-id",
                actual_currency="INR",
                actual_mappings_file=str(base / "unused.json"),
                actual_data_dir=str(base / "cache"),
                actual_api_module=str(fake_api),
                actual_bridge_command=(shutil.which("node") or "node", str(bridge_script)),
            )
            bridge = ActualBridge(settings)

            bridge.check(("account-one",))
            bridge.import_transaction(
                {
                    "account_id": "account-one",
                    "transaction": {"imported_id": "sms-v1:test"},
                }
            )

    def test_bundled_bridge_rejects_missing_mapped_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fake_api = base / "fake-actual-api.cjs"
            fake_api.write_text(
                """
module.exports = {
  init: async () => {},
  downloadBudget: async () => {},
  getAccounts: async () => [],
  shutdown: async () => {},
};
""",
                encoding="utf-8",
            )
            bridge_script = Path(__file__).parents[1] / "pi_backend" / "actual_bridge.cjs"
            settings = base_settings(
                actual_server_url="http://127.0.0.1:5006",
                actual_password="actual-password",
                actual_sync_id="budget-sync-id",
                actual_currency="INR",
                actual_mappings_file=str(base / "unused.json"),
                actual_data_dir=str(base / "cache"),
                actual_api_module=str(fake_api),
                actual_bridge_command=(shutil.which("node") or "node", str(bridge_script)),
            )

            with self.assertRaisesRegex(ActualBridgeError, "account_not_found"):
                ActualBridge(settings).check(("missing",))


if __name__ == "__main__":
    unittest.main()
