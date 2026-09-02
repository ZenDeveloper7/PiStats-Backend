from __future__ import annotations

from contextlib import closing
import http.client
import json
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
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

    def get(
        self,
        path: str,
        *,
        token: str = TOKEN,
    ) -> tuple:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "GET",
            path,
            headers={"Authorization": f"Bearer {token}"},
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
        request_id: str | None = None,
        include_headers: bool = False,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "Idempotency-Key": event_id,
            "X-PiStats-Device-Id": DEVICE_ID,
        }
        if request_id is not None:
            headers["X-PiStats-Request-Id"] = request_id
        connection.request(
            "POST",
            "/api/transactions/sms",
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        response_body = response.read()
        result: tuple = (
            response.status,
            json.loads(response_body) if response_body else {},
        )
        if include_headers:
            result += (dict(response.getheaders()),)
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
                            "label": "HDFC Savings •1234",
                            "sender": "HDFCBK",
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

    def test_lists_safe_labeled_account_options(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.get("/api/transactions/accounts")

        self.assertEqual(status, 200)
        self.assertEqual(len(payload["accounts"]), 1)
        account = payload["accounts"][0]
        self.assertEqual(account["label"], "HDFC Savings •1234")
        self.assertEqual(account["sender_contains"], "HDFCBK")
        self.assertEqual(account["account_hint"], "1234")
        self.assertRegex(account["mapping_id"], r"^[0-9a-f-]{36}$")
        self.assertNotIn("actual_account_id", account)

    def test_account_options_require_bearer_auth(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.get(
                "/api/transactions/accounts",
                token="wrong",
            )

        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "unauthorized"})

    def test_account_options_are_disabled_without_actual_configuration(self) -> None:
        service = TransactionSyncService(base_settings(), self.bridge)
        with RunningServer(base_settings(), service) as server:
            status, payload = server.get("/api/transactions/accounts")

        self.assertEqual(status, 403)
        self.assertEqual(payload, {"error": "transaction_sync_not_configured"})

    def test_selected_account_mapping_is_used_for_import(self) -> None:
        mapping_id = self.service.account_options()[0]["mapping_id"]
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(account_mapping_id=mapping_id))

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertEqual(self.bridge.imports[0]["account_id"], "actual-account-id")

    def test_request_id_is_echoed_logged_and_persisted_without_transaction_data(self) -> None:
        request_id = "d5675780-a6ad-4e3d-b1f6-35c6703bc123"
        with self.assertLogs("pistats.transaction_sync", level="INFO") as logs:
            with RunningServer(self.settings, self.service) as server:
                status, payload, headers = server.post(
                    event(),
                    request_id=request_id,
                    include_headers=True,
                )

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertEqual(headers["X-PiStats-Request-Id"], request_id)
        self.assertIn(f"request_id={request_id}", logs.output[0])
        self.assertIn("outcome=imported", logs.output[0])
        self.assertNotIn("SWIGGY", logs.output[0])
        self.assertNotIn("24550", logs.output[0])
        with closing(sqlite3.connect(self.base / "transactions.sqlite3")) as connection:
            stored_request_id = connection.execute(
                "SELECT request_id FROM transaction_imports"
            ).fetchone()[0]
        self.assertEqual(stored_request_id, request_id)

    def test_existing_import_database_is_migrated_for_request_ids(self) -> None:
        database = self.base / "transactions.sqlite3"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE transaction_imports (
                    idempotency_key TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    actual_account_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    error_code TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )
        request_id = "e891d546-034d-4ec6-bacf-0ed69979bcbb"

        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(), request_id=request_id)

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT request_id, state FROM transaction_imports"
            ).fetchone()
        self.assertEqual(row, (request_id, "completed"))

    def test_invalid_request_id_is_replaced_with_a_safe_generated_id(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload, headers = server.post(
                event(),
                request_id="not-a-uuid",
                include_headers=True,
            )

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertNotEqual(headers["X-PiStats-Request-Id"], "not-a-uuid")
        self.assertEqual(
            str(uuid.UUID(headers["X-PiStats-Request-Id"])),
            headers["X-PiStats-Request-Id"],
        )

    def test_unknown_selected_account_mapping_is_rejected(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(
                event(account_mapping_id="00000000-0000-0000-0000-000000000000")
            )

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "account_mapping_not_found"})
        self.assertEqual(self.bridge.imports, [])

    def test_selected_mapping_must_still_match_sender_and_account_hint(self) -> None:
        mapping_id = self.service.account_options()[0]["mapping_id"]
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(
                event(
                    account_mapping_id=mapping_id,
                    sender="VM-OTHERBK",
                    account_hint="9999",
                )
            )

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "account_mapping_not_found"})
        self.assertEqual(self.bridge.imports, [])

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
        with closing(sqlite3.connect(self.base / "transactions.sqlite3")) as connection:
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

    def test_sender_mapping_is_case_insensitive_substring(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(sender="ad-hdfcbk-alerts"))

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertEqual(len(self.bridge.imports), 1)

    def test_sender_without_configured_fragment_is_rejected(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, payload = server.post(event(sender="VM-OTHERBK"))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "account_mapping_not_found"})
        self.assertEqual(self.bridge.imports, [])

    def test_ambiguous_sender_fragments_for_different_accounts_are_rejected(self) -> None:
        self.mappings.write_text(
            json.dumps(
                {
                    "mappings": [
                        {
                            "sender": "HDFCBK",
                            "account_hint": "1234",
                            "actual_account_id": "first-account",
                        },
                        {
                            "sender": "BANK",
                            "account_hint": "1234",
                            "actual_account_id": "second-account",
                        },
                    ]
                }
            ),
            encoding="utf-8",
        )
        service = TransactionSyncService(self.settings, self.bridge)

        with RunningServer(self.settings, service) as server:
            status, payload = server.post(event(sender="VM-HDFCBK-BANK"))

        self.assertEqual(status, 422)
        self.assertEqual(payload, {"error": "account_mapping_ambiguous"})
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

    def test_bank_reference_may_be_omitted(self) -> None:
        payload = event()
        del payload["bank_reference"]

        with RunningServer(self.settings, self.service) as server:
            status, response = server.post(payload)

        self.assertEqual((status, response), (201, {"status": "imported"}))
        self.assertIsNone(self.bridge.imports[0]["transaction"]["notes"])

    def test_bank_reference_may_be_null(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, response = server.post(event(bank_reference=None))

        self.assertEqual((status, response), (201, {"status": "imported"}))
        self.assertIsNone(self.bridge.imports[0]["transaction"]["notes"])

    def test_account_hint_is_required(self) -> None:
        payload = event()
        del payload["account_hint"]

        with RunningServer(self.settings, self.service) as server:
            status, response = server.post(payload)

        self.assertEqual(status, 422)
        self.assertEqual(response, {"error": "invalid_transaction_event"})
        self.assertEqual(self.bridge.imports, [])

    def test_account_hint_cannot_be_null(self) -> None:
        with RunningServer(self.settings, self.service) as server:
            status, response = server.post(event(account_hint=None))

        self.assertEqual(status, 422)
        self.assertEqual(response, {"error": "invalid_account_hint"})
        self.assertEqual(self.bridge.imports, [])

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
        request_id = "21f6897e-607c-4488-b1c8-087140866ac2"
        with self.assertLogs("pistats.transaction_sync", level="INFO") as logs:
            with RunningServer(self.settings, self.service) as server:
                status, payload = server.post(event(), request_id=request_id)
                self.assertEqual(status, 502)
                self.assertEqual(payload, {"error": "actual_import_failed"})
                self.bridge.error = None
                status, payload = server.post(event())

        self.assertEqual((status, payload), (201, {"status": "imported"}))
        self.assertEqual(len(self.bridge.imports), 1)
        self.assertTrue(
            any(
                f"request_id={request_id}" in line
                and "outcome=failed" in line
                and "code=network" in line
                for line in logs.output
            )
        )

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
    def test_mapping_requires_a_non_null_account_hint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mappings = base / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "sender": "HDFCBK",
                                "account_hint": None,
                                "actual_account_id": "one",
                            }
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

            with self.assertRaisesRegex(ValueError, "invalid mapping"):
                TransactionSyncService(settings, FakeBridge())

    def test_legacy_mapping_gets_a_safe_default_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mappings = base / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "sender": "HDFCBK",
                                "account_hint": "1234",
                                "actual_account_id": "one",
                            }
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

            service = TransactionSyncService(settings, FakeBridge())

        self.assertEqual(service.account_options()[0]["label"], "HDFCBK ••••1234")

    def test_maximum_length_legacy_mapping_gets_a_bounded_default_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mappings = base / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "sender": "S" * 32,
                                "account_hint": "1" * 64,
                                "actual_account_id": "one",
                            }
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

            service = TransactionSyncService(settings, FakeBridge())

        label = service.account_options()[0]["label"]
        self.assertEqual(len(label), 80)
        self.assertTrue(label.startswith("S" * 32))

    def test_blank_account_label_fails_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mappings = base / "mappings.json"
            mappings.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "label": "   ",
                                "sender": "HDFCBK",
                                "account_hint": "1234",
                                "actual_account_id": "one",
                            }
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

            with self.assertRaisesRegex(ValueError, "invalid mapping"):
                TransactionSyncService(settings, FakeBridge())

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
