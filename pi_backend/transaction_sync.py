from __future__ import annotations

from contextlib import closing
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol

from .config import Settings


MAX_REQUEST_BYTES = 16_384
MAX_AMOUNT_MINOR = 9_000_000_000_000_000
MAX_TIMESTAMP_MILLIS = 253_402_300_799_999
EVENT_ID_PATTERN = r"sms-v1:[0-9a-f]{64}"
RESULT_PREFIX = "PISTATS_RESULT:"


class TransactionSyncError(Exception):
    def __init__(
        self,
        status: HTTPStatus,
        code: str,
        *,
        diagnostic_code: str | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.diagnostic_code = diagnostic_code or code


class ActualBridgeError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class AccountMapping:
    mapping_id: str
    label: str
    sender: str
    account_hint: str
    actual_account_id: str


@dataclass(frozen=True)
class TransactionEvent:
    event_id: str
    received_at_millis: int
    transaction_date: str | None
    transaction_time: str | None
    amount_minor: int
    currency: str
    direction: str
    payee: str
    account_mapping_id: str | None
    account_hint: str
    bank_reference: str | None
    sender: str
    source: str
    cleared: bool
    device_id: str
    idempotency_key: str

    @property
    def actual_date(self) -> str:
        if self.transaction_date is not None:
            return self.transaction_date
        try:
            return datetime.fromtimestamp(self.received_at_millis / 1000).date().isoformat()
        except (OSError, OverflowError, ValueError) as exc:
            raise TransactionSyncError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_received_at_millis",
            ) from exc

    def bridge_payload(self, actual_account_id: str) -> dict[str, Any]:
        notes = None
        if self.bank_reference:
            notes = f"Bank reference: {self.bank_reference}"
        return {
            "account_id": actual_account_id,
            "transaction": {
                "date": self.actual_date,
                "amount": self.amount_minor,
                "payee_name": self.payee,
                "notes": notes,
                "cleared": False,
                "imported_id": self.event_id,
            },
        }


class Bridge(Protocol):
    def check(self, account_ids: tuple[str, ...]) -> None: ...

    def import_transaction(self, payload: dict[str, Any]) -> None: ...


class ActualBridge:
    def __init__(self, settings: Settings) -> None:
        self._command = settings.actual_bridge_command
        self._timeout = settings.actual_timeout_seconds
        self._data_dir = Path(
            settings.actual_data_dir or "./state/actual-cache"
        ).expanduser()
        self._environment = self._build_environment(settings, self._data_dir)

    def check(self, account_ids: tuple[str, ...]) -> None:
        self._run({"action": "check", "account_ids": list(account_ids)})

    def import_transaction(self, payload: dict[str, Any]) -> None:
        self._run({"action": "import", **payload})

    def _run(self, payload: dict[str, Any]) -> None:
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self._data_dir, 0o700)
            result = subprocess.run(
                self._command,
                input=json.dumps(payload, separators=(",", ":")),
                capture_output=True,
                text=True,
                check=False,
                timeout=self._timeout,
                env=self._environment,
            )
        except FileNotFoundError as exc:
            raise ActualBridgeError("actual_bridge_not_found") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise ActualBridgeError("actual_bridge_failed") from exc

        response = _parse_bridge_result(result.stdout)
        if response is None:
            raise ActualBridgeError("actual_bridge_invalid_response")
        if result.returncode != 0 or response.get("ok") is not True:
            code = response.get("code")
            if not isinstance(code, str) or re.fullmatch(r"[a-z0-9_]{1,64}", code) is None:
                code = "actual_api_error"
            raise ActualBridgeError(code)

    @staticmethod
    def _build_environment(settings: Settings, data_dir: Path) -> dict[str, str]:
        environment: dict[str, str] = {}
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR", "NODE_EXTRA_CA_CERTS"):
            value = os.environ.get(key)
            if value:
                environment[key] = value
        environment.update(
            {
                "PISTATS_ACTUAL_SERVER_URL": settings.actual_server_url or "",
                "PISTATS_ACTUAL_PASSWORD": settings.actual_password or "",
                "PISTATS_ACTUAL_SYNC_ID": settings.actual_sync_id or "",
                "PISTATS_ACTUAL_DATA_DIR": str(data_dir),
                "PISTATS_ACTUAL_API_MODULE": settings.actual_api_module,
            }
        )
        if settings.actual_encryption_password:
            environment["PISTATS_ACTUAL_ENCRYPTION_PASSWORD"] = (
                settings.actual_encryption_password
            )
        return environment


class TransactionSyncService:
    def __init__(self, settings: Settings, bridge: Bridge | None = None) -> None:
        self.configured = settings.actual_budget_configured
        self._database = Path(
            settings.transaction_database or "./state/transactions.sqlite3"
        ).expanduser()
        self._health_cache_seconds = settings.actual_health_cache_seconds
        self._actual_currency = settings.actual_currency
        self._health_checked_at = 0.0
        self._healthy = False
        self._lock = threading.RLock()
        self._database_initialized = False
        self._mappings: dict[tuple[str, str | None], AccountMapping] = {}
        if self.configured:
            self._mappings = _load_mappings(Path(settings.actual_mappings_file or ""))
        self._bridge = bridge or ActualBridge(settings)

    def is_healthy(self) -> bool:
        if not self.configured:
            return False
        with self._lock:
            now = time.monotonic()
            if now - self._health_checked_at < self._health_cache_seconds:
                return self._healthy
            try:
                self._bridge.check(self.account_ids)
            except ActualBridgeError:
                self._healthy = False
            else:
                self._healthy = True
            self._health_checked_at = now
            return self._healthy

    @property
    def account_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(mapping.actual_account_id for mapping in self._mappings.values())
        )

    def account_options(self) -> list[dict[str, str | None]]:
        if not self.configured:
            raise TransactionSyncError(
                HTTPStatus.FORBIDDEN,
                "transaction_sync_not_configured",
            )
        return [
            {
                "mapping_id": mapping.mapping_id,
                "label": mapping.label,
                "sender_contains": mapping.sender,
                "account_hint": mapping.account_hint,
            }
            for mapping in self._mappings.values()
        ]

    def import_event(self, event: TransactionEvent, request_id: str) -> bool:
        if not self.configured:
            raise TransactionSyncError(
                HTTPStatus.FORBIDDEN,
                "transaction_sync_not_configured",
            )
        if event.currency != self._actual_currency:
            raise TransactionSyncError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "transaction_currency_mismatch",
            )
        mapping = _find_mapping(
            self._mappings,
            event.sender,
            event.account_hint,
            event.account_mapping_id,
        )
        if mapping is None:
            raise TransactionSyncError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "account_mapping_not_found",
            )

        with self._lock:
            self._initialize_database()
            if self._is_completed(event.idempotency_key):
                return False
            self._record_processing(event, mapping.actual_account_id, request_id)
            try:
                self._bridge.import_transaction(
                    event.bridge_payload(mapping.actual_account_id)
                )
            except ActualBridgeError as exc:
                self._record_failure(event.idempotency_key, exc.code)
                self._healthy = False
                self._health_checked_at = time.monotonic()
                if exc.code == "account_not_found":
                    raise TransactionSyncError(
                        HTTPStatus.UNPROCESSABLE_ENTITY,
                        "account_mapping_not_found",
                    ) from exc
                raise TransactionSyncError(
                    HTTPStatus.BAD_GATEWAY,
                    "actual_import_failed",
                    diagnostic_code=exc.code,
                ) from exc
            self._record_completed(event.idempotency_key)
            self._healthy = True
            self._health_checked_at = time.monotonic()
            return True

    def _initialize_database(self) -> None:
        if self._database_initialized:
            return
        self._database.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        with closing(sqlite3.connect(self._database)) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS transaction_imports (
                    idempotency_key TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    actual_account_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
                    error_code TEXT,
                    request_id TEXT,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(transaction_imports)")
            }
            if "request_id" not in columns:
                connection.execute(
                    "ALTER TABLE transaction_imports ADD COLUMN request_id TEXT"
                )
        os.chmod(self._database, 0o600)
        self._database_initialized = True

    def _is_completed(self, idempotency_key: str) -> bool:
        with closing(sqlite3.connect(self._database)) as connection:
            row = connection.execute(
                "SELECT state FROM transaction_imports WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return row is not None and row[0] == "completed"

    def _record_processing(
        self,
        event: TransactionEvent,
        actual_account_id: str,
        request_id: str,
    ) -> None:
        with closing(sqlite3.connect(self._database)) as connection, connection:
            connection.execute(
                """
                INSERT INTO transaction_imports (
                    idempotency_key, event_id, device_id, actual_account_id,
                    state, error_code, request_id, updated_at
                ) VALUES (?, ?, ?, ?, 'processing', NULL, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    state = 'processing',
                    error_code = NULL,
                    request_id = excluded.request_id,
                    updated_at = excluded.updated_at
                WHERE transaction_imports.state != 'completed'
                """,
                (
                    event.idempotency_key,
                    event.event_id,
                    event.device_id,
                    actual_account_id,
                    request_id,
                    int(time.time()),
                ),
            )

    def _record_failure(self, idempotency_key: str, code: str) -> None:
        with closing(sqlite3.connect(self._database)) as connection, connection:
            connection.execute(
                """
                UPDATE transaction_imports
                SET state = 'failed', error_code = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (code[:128], int(time.time()), idempotency_key),
            )

    def _record_completed(self, idempotency_key: str) -> None:
        with closing(sqlite3.connect(self._database)) as connection, connection:
            connection.execute(
                """
                UPDATE transaction_imports
                SET state = 'completed', error_code = NULL, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (int(time.time()), idempotency_key),
            )


def parse_transaction_request(
    headers: Mapping[str, str],
    body: BinaryIO,
) -> TransactionEvent:
    if headers.get("Transfer-Encoding"):
        raise TransactionSyncError(
            HTTPStatus.BAD_REQUEST,
            "transfer_encoding_not_supported",
        )
    content_type = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise TransactionSyncError(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            "content_type_must_be_application_json",
        )
    try:
        content_length = int(headers.get("Content-Length", ""))
    except ValueError as exc:
        raise TransactionSyncError(
            HTTPStatus.BAD_REQUEST,
            "invalid_content_length",
        ) from exc
    if content_length <= 0:
        raise TransactionSyncError(
            HTTPStatus.BAD_REQUEST,
            "invalid_content_length",
        )
    if content_length > MAX_REQUEST_BYTES:
        raise TransactionSyncError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "request_too_large",
        )
    payload_bytes = body.read(content_length)
    if len(payload_bytes) != content_length:
        raise TransactionSyncError(
            HTTPStatus.BAD_REQUEST,
            "incomplete_request_body",
        )
    try:
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransactionSyncError(HTTPStatus.BAD_REQUEST, "invalid_json") from exc
    return _parse_event(headers, payload)


def _parse_event(headers: Mapping[str, str], payload: Any) -> TransactionEvent:
    required = {
        "event_id",
        "received_at_millis",
        "amount_minor",
        "currency",
        "direction",
        "payee",
        "account_hint",
        "sender",
        "source",
        "cleared",
    }
    optional = {
        "transaction_date",
        "transaction_time",
        "account_mapping_id",
        "bank_reference",
    }
    if (
        not isinstance(payload, dict)
        or not required.issubset(payload)
        or not set(payload).issubset(required | optional)
    ):
        raise TransactionSyncError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_transaction_event",
        )

    event_id = _required_string(payload["event_id"], 71, "event_id")
    if re.fullmatch(EVENT_ID_PATTERN, event_id) is None:
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_event_id")
    idempotency_key = headers.get("Idempotency-Key", "")
    if idempotency_key != event_id:
        raise TransactionSyncError(
            HTTPStatus.BAD_REQUEST,
            "idempotency_key_mismatch",
        )
    raw_device_id = headers.get("X-PiStats-Device-Id", "")
    try:
        device_id = str(uuid.UUID(raw_device_id))
    except ValueError as exc:
        raise TransactionSyncError(HTTPStatus.BAD_REQUEST, "invalid_device_id") from exc
    if device_id != raw_device_id.lower():
        raise TransactionSyncError(HTTPStatus.BAD_REQUEST, "invalid_device_id")

    received_at_millis = _required_int(
        payload["received_at_millis"], "received_at_millis"
    )
    if received_at_millis < 0 or received_at_millis > MAX_TIMESTAMP_MILLIS:
        raise TransactionSyncError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_received_at_millis",
        )
    amount_minor = _required_int(payload["amount_minor"], "amount_minor")
    if amount_minor == 0 or abs(amount_minor) > MAX_AMOUNT_MINOR:
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_amount_minor")
    direction = _required_string(payload["direction"], 6, "direction").lower()
    if direction not in {"debit", "credit"} or (
        direction == "debit" and amount_minor > 0
    ) or (direction == "credit" and amount_minor < 0):
        raise TransactionSyncError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "amount_direction_mismatch",
        )
    currency = _required_string(payload["currency"], 3, "currency").upper()
    if len(currency) != 3 or not currency.isalpha() or not currency.isascii():
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_currency")
    payee = _required_string(payload["payee"], 160, "payee")
    sender = _required_string(payload["sender"], 32, "sender")
    account_mapping_id = _optional_string(
        payload.get("account_mapping_id"), 64, "account_mapping_id"
    )
    account_hint = _required_string(payload["account_hint"], 64, "account_hint")
    bank_reference = _optional_string(
        payload.get("bank_reference"), 128, "bank_reference"
    )
    transaction_date = _optional_string(
        payload.get("transaction_date"), 10, "transaction_date"
    )
    if transaction_date is not None:
        try:
            date.fromisoformat(transaction_date)
        except ValueError as exc:
            raise TransactionSyncError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_transaction_date",
            ) from exc
    transaction_time = _optional_string(
        payload.get("transaction_time"), 8, "transaction_time"
    )
    if transaction_time is not None:
        try:
            datetime.strptime(transaction_time, "%H:%M:%S")
        except ValueError as exc:
            raise TransactionSyncError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_transaction_time",
            ) from exc
    if payload["source"] != "bank_sms" or payload["cleared"] is not False:
        raise TransactionSyncError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_transaction_event",
        )

    return TransactionEvent(
        event_id=event_id,
        received_at_millis=received_at_millis,
        transaction_date=transaction_date,
        transaction_time=transaction_time,
        amount_minor=amount_minor,
        currency=currency,
        direction=direction,
        payee=payee,
        account_mapping_id=account_mapping_id,
        account_hint=account_hint,
        bank_reference=bank_reference,
        sender=sender,
        source="bank_sms",
        cleared=False,
        device_id=device_id,
        idempotency_key=idempotency_key,
    )


def _required_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, f"invalid_{name}")
    return value


def _required_string(value: Any, maximum: int, name: str) -> str:
    if not isinstance(value, str):
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, f"invalid_{name}")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or _contains_control_character(cleaned):
        raise TransactionSyncError(HTTPStatus.UNPROCESSABLE_ENTITY, f"invalid_{name}")
    return cleaned


def _optional_string(value: Any, maximum: int, name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, maximum, name)


def _contains_control_character(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _load_mappings(path: Path) -> dict[tuple[str, str], AccountMapping]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("PISTATS_ACTUAL_MAPPINGS_FILE does not exist") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read PISTATS_ACTUAL_MAPPINGS_FILE") from exc
    if not isinstance(payload, dict) or set(payload) != {"mappings"}:
        raise ValueError("PISTATS_ACTUAL_MAPPINGS_FILE has invalid structure")
    rows = payload["mappings"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("PISTATS_ACTUAL_MAPPINGS_FILE must contain mappings")

    mappings: dict[tuple[str, str], AccountMapping] = {}
    for row in rows:
        required_keys = {"sender", "account_hint", "actual_account_id"}
        if (
            not isinstance(row, dict)
            or not required_keys.issubset(row)
            or not set(row).issubset(required_keys | {"label"})
        ):
            raise ValueError("PISTATS_ACTUAL_MAPPINGS_FILE has an invalid mapping")
        try:
            sender = _normalize_sender(row["sender"])
            hint = _normalize_hint(row["account_hint"])
            account_id = _mapping_string(row["actual_account_id"], 128)
            if "label" in row:
                label = _mapping_string(row["label"], 80)
            else:
                label = _default_account_label(sender, hint)[:80]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "PISTATS_ACTUAL_MAPPINGS_FILE has an invalid mapping"
            ) from exc
        key = (sender, hint)
        if key in mappings:
            raise ValueError("PISTATS_ACTUAL_MAPPINGS_FILE has a duplicate mapping")
        mappings[key] = AccountMapping(
            mapping_id=_account_mapping_id(sender, hint, account_id),
            label=label,
            sender=sender,
            account_hint=hint,
            actual_account_id=account_id,
        )
    return mappings


def _mapping_string(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError("mapping value must be a string")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or _contains_control_character(cleaned):
        raise ValueError("invalid mapping value")
    return cleaned


def _normalize_sender(value: Any) -> str:
    return _mapping_string(value, 32).upper()


def _normalize_hint(value: Any) -> str:
    return _mapping_string(value, 64).upper()


def _default_account_label(sender: str, account_hint: str) -> str:
    return f"{sender} ••••{account_hint}"


def _account_mapping_id(
    sender: str,
    account_hint: str,
    actual_account_id: str,
) -> str:
    name = f"pistats-actual-account:{sender}:{account_hint}:{actual_account_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def _find_mapping(
    mappings: Mapping[tuple[str, str], AccountMapping],
    sender: str,
    account_hint: str,
    mapping_id: str | None = None,
) -> AccountMapping | None:
    normalized_sender = _normalize_sender(sender)
    normalized_hint = _normalize_hint(account_hint)
    matches = [
        mapping
        for (sender_fragment, mapped_hint), mapping in mappings.items()
        if (
            (mapping_id is None or mapping.mapping_id == mapping_id)
            and mapped_hint == normalized_hint
            and sender_fragment in normalized_sender
        )
    ]
    if not matches:
        return None
    if len({mapping.actual_account_id for mapping in matches}) > 1:
        raise TransactionSyncError(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "account_mapping_ambiguous",
        )
    return matches[0]


def _parse_bridge_result(stdout: str) -> dict[str, Any] | None:
    if len(stdout) > 1_048_576:
        return None
    for line in reversed(stdout.splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            payload = json.loads(line[len(RESULT_PREFIX) :])
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None
    return None
