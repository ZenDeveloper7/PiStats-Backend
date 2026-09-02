from __future__ import annotations

import hmac
import json
import logging
import os
import re
import socket
import sqlite3
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .collectors import StatsCollector
from .config import Settings, load_settings
from .media_backup import (
    MIME_EXTENSIONS,
    MediaBackupError,
    MediaBackupService,
    UploadRequest,
)
from .transaction_sync import (
    TransactionSyncError,
    TransactionSyncService,
    parse_transaction_request,
)


TRANSACTION_REQUEST_ID_HEADER = "X-PiStats-Request-Id"
transaction_logger = logging.getLogger("pistats.transaction_sync")


def create_handler(
    settings: Settings,
    *,
    transaction_sync_service: TransactionSyncService | None = None,
) -> type[BaseHTTPRequestHandler]:
    collector = StatsCollector(settings)
    media_backup = MediaBackupService(settings) if settings.media_backup_root else None
    wake_on_lan = WakeOnLanController(settings)
    transaction_sync = transaction_sync_service or TransactionSyncService(settings)

    class PiStatsHandler(BaseHTTPRequestHandler):
        server_version = "OwnNode/1.0"

        def do_GET(self) -> None:  # noqa: N802
            if not self._is_authorized():
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                )
                return

            request = urlsplit(self.path)

            if request.path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "api_version": 1,
                        "status": "ok",
                        "features": {
                            "stats": True,
                            "wakeonlan": wake_on_lan.configured,
                            "wakeonlan_control": True,
                            "media_backup": media_backup is not None,
                            "backup_drive": bool(
                                settings.backup_label or settings.backup_mountpoint
                            ),
                            "docker_services": True,
                            "service_selection": True,
                            "transaction_sync": True,
                            "actual_budget": transaction_sync.is_healthy(),
                        },
                    },
                )
                return

            if request.path == "/api/wakeonlan/settings":
                self._send_json(
                    HTTPStatus.OK,
                    wake_on_lan.status(),
                )
                return

            if request.path == "/api/transactions/accounts":
                try:
                    accounts = transaction_sync.account_options()
                except TransactionSyncError as exc:
                    self._send_json(exc.status, {"error": exc.code})
                    return
                self._send_json(HTTPStatus.OK, {"accounts": accounts})
                return

            if request.path == "/api/services":
                self._send_json(
                    HTTPStatus.OK,
                    {"services": collector.list_services()},
                )
                return

            if request.path == "/api/stats":
                try:
                    selected_services = _parse_service_selection(request.query)
                except ValueError:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_services"},
                    )
                    return
                self._send_json(
                    HTTPStatus.OK,
                    collector.collect_all(selected_services),
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            request_id = (
                _transaction_request_id(self.headers.get(TRANSACTION_REQUEST_ID_HEADER))
                if path == "/api/transactions/sms"
                else None
            )
            if not self._is_authorized(
                allow_wake_token=path == "/api/wakeonlan/wake"
            ):
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                    request_id=request_id,
                )
                if request_id is not None:
                    _log_transaction_sync(request_id, "rejected", "unauthorized")
                return

            if path == "/api/wakeonlan/wake":
                try:
                    wake_on_lan.wake(settings)
                except WakeOnLanError as exc:
                    self._send_json(
                        exc.status,
                        {"status": "failed", "error": str(exc)},
                    )
                    return
                except OSError:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"status": "failed", "error": "wake_packet_send_failed"},
                    )
                    return

                self._send_json(
                    HTTPStatus.OK,
                    {
                        "status": "sent",
                        "broadcast": settings.wake_broadcast,
                        "port": settings.wake_port,
                    },
                )
                return

            if path == "/api/transactions/sms":
                assert request_id is not None
                try:
                    event = parse_transaction_request(self.headers, self.rfile)
                    imported = transaction_sync.import_event(event, request_id)
                except TransactionSyncError as exc:
                    self._send_json(
                        exc.status,
                        {"error": exc.code},
                        request_id=request_id,
                    )
                    _log_transaction_sync(
                        request_id,
                        "rejected" if exc.status.value < 500 else "failed",
                        exc.diagnostic_code,
                    )
                    return
                except (OSError, sqlite3.Error):
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "transaction_state_failed"},
                        request_id=request_id,
                    )
                    _log_transaction_sync(
                        request_id,
                        "failed",
                        "transaction_state_failed",
                    )
                    return
                if not imported:
                    self._send_json(
                        HTTPStatus.CONFLICT,
                        {"status": "already_imported"},
                        request_id=request_id,
                    )
                    _log_transaction_sync(request_id, "already_imported")
                    return
                self._send_json(
                    HTTPStatus.CREATED,
                    {"status": "imported"},
                    request_id=request_id,
                )
                _log_transaction_sync(request_id, "imported")
                return

            if path == "/api/media/backup/items":
                if media_backup is None:
                    self._send_json(
                        HTTPStatus.NOT_FOUND,
                        {"error": "media_backup_not_installed"},
                    )
                    return
                try:
                    upload = self._parse_media_upload()
                    previous_timeout = self.connection.gettimeout()
                    self.connection.settimeout(
                        settings.media_backup_read_timeout_seconds
                    )
                    try:
                        media_backup.store(upload, self.rfile)
                    finally:
                        self.connection.settimeout(previous_timeout)
                except socket.timeout:
                    self.close_connection = True
                    self._send_json(
                        HTTPStatus.REQUEST_TIMEOUT,
                        {"error": "upload_timeout"},
                    )
                    return
                except MediaBackupError as exc:
                    self.close_connection = True
                    self._send_json(HTTPStatus(exc.status), {"error": exc.code})
                    return
                except (OSError, sqlite3.Error):
                    self.close_connection = True
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"error": "media_backup_failed"},
                    )
                    return
                self._send_json(HTTPStatus.CREATED, {"status": "stored"})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_PUT(self) -> None:  # noqa: N802
            if not self._is_authorized():
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                )
                return

            path = urlsplit(self.path).path
            if path != "/api/wakeonlan/settings":
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return

            try:
                enabled = self._parse_wake_on_lan_setting()
                wake_on_lan.set_enabled(enabled)
            except WakeOnLanError as exc:
                self._send_json(exc.status, {"error": str(exc)})
                return
            except OSError:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "wake_state_write_failed"},
                )
                return

            self._send_json(HTTPStatus.OK, wake_on_lan.status())

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _is_authorized(self, *, allow_wake_token: bool = False) -> bool:
            if settings.dev_mode:
                return True
            auth_header = self.headers.get("Authorization", "")
            expected = f"Bearer {settings.token}"
            if settings.token and _tokens_equal(auth_header, expected):
                return True
            if not allow_wake_token or not settings.token:
                return False
            wake_token_header = self.headers.get("X-Wake-Token", "")
            return _tokens_equal(wake_token_header, settings.token)

        def _parse_media_upload(self) -> UploadRequest:
            if self.headers.get("Transfer-Encoding"):
                raise MediaBackupError(400, "transfer_encoding_not_supported")

            raw_length = self.headers.get("Content-Length")
            try:
                content_length = int(raw_length or "")
            except ValueError as exc:
                raise MediaBackupError(400, "invalid_content_length") from exc
            if content_length < 0:
                raise MediaBackupError(400, "invalid_content_length")
            if content_length > settings.media_backup_max_bytes:
                raise MediaBackupError(413, "file_too_large")

            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)

            def required(name: str) -> str:
                values = query.get(name)
                if not values or len(values) != 1 or not values[0]:
                    raise MediaBackupError(400, f"invalid_{name}")
                return values[0]

            display_name = required("display_name")
            if len(display_name) > 255:
                raise MediaBackupError(400, "invalid_display_name")
            mime_type = required("mime_type").lower()
            request_content_type = self.headers.get_content_type().lower()
            if mime_type not in MIME_EXTENSIONS or request_content_type != mime_type:
                raise MediaBackupError(415, "unsupported_media_type")

            size_bytes = _parse_nonnegative_int(required("size_bytes"), "size_bytes")
            if size_bytes > settings.media_backup_max_bytes:
                raise MediaBackupError(413, "file_too_large")
            if size_bytes != content_length:
                raise MediaBackupError(400, "content_length_mismatch")
            modified_at = _parse_nonnegative_int(
                required("modified_at_seconds"), "modified_at_seconds"
            )
            captured_raw = query.get("captured_at_millis")
            captured_at = None
            if captured_raw is not None:
                if len(captured_raw) != 1:
                    raise MediaBackupError(400, "invalid_captured_at_millis")
                captured_at = _parse_nonnegative_int(
                    captured_raw[0], "captured_at_millis"
                )

            device_id = self.headers.get("X-PiStats-Device-Id", "")
            try:
                parsed_device_id = str(uuid.UUID(device_id))
            except ValueError as exc:
                raise MediaBackupError(400, "invalid_device_id") from exc
            if parsed_device_id != device_id.lower():
                raise MediaBackupError(400, "invalid_device_id")

            media_key = self.headers.get("X-PiStats-Media-Key", "")
            idempotency_key = self.headers.get("Idempotency-Key", "")
            if not media_key or len(media_key) > 512:
                raise MediaBackupError(400, "invalid_media_key")
            if not idempotency_key or len(idempotency_key) > 768:
                raise MediaBackupError(400, "invalid_idempotency_key")

            relative_values = query.get("relative_path")
            if relative_values is not None and len(relative_values) != 1:
                raise MediaBackupError(400, "invalid_relative_path")
            relative_path = relative_values[0] if relative_values else None
            if relative_path is not None and len(relative_path) > 4096:
                raise MediaBackupError(400, "invalid_relative_path")

            return UploadRequest(
                display_name=display_name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                modified_at_seconds=modified_at,
                captured_at_millis=captured_at,
                relative_path=relative_path,
                device_id=parsed_device_id,
                media_key=media_key,
                idempotency_key=idempotency_key,
            )

        def _parse_wake_on_lan_setting(self) -> bool:
            if self.headers.get_content_type() != "application/json":
                raise WakeOnLanError(
                    "content_type_must_be_application_json",
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
            raw_length = self.headers.get("Content-Length")
            try:
                content_length = int(raw_length or "")
            except ValueError as exc:
                raise WakeOnLanError("invalid_content_length") from exc
            if content_length <= 0 or content_length > 1_024:
                raise WakeOnLanError("invalid_content_length")

            payload_bytes = self.rfile.read(content_length)
            if len(payload_bytes) != content_length:
                raise WakeOnLanError("incomplete_request_body")
            try:
                payload = json.loads(payload_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise WakeOnLanError("invalid_json") from exc
            if (
                not isinstance(payload, dict)
                or set(payload) != {"enabled"}
                or not isinstance(payload["enabled"], bool)
            ):
                raise WakeOnLanError("invalid_wake_settings")
            return payload["enabled"]

        def _send_json(
            self,
            status: HTTPStatus,
            body: dict[str, Any],
            *,
            request_id: str | None = None,
        ) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if request_id is not None:
                self.send_header(TRANSACTION_REQUEST_ID_HEADER, request_id)
            self.end_headers()
            self.wfile.write(payload)

        @classmethod
        def close_services(cls) -> None:
            if media_backup is not None:
                media_backup.close()

    return PiStatsHandler


class WakeOnLanError(Exception):
    def __init__(
        self,
        code: str,
        status: HTTPStatus = HTTPStatus.BAD_REQUEST,
    ) -> None:
        super().__init__(code)
        self.status = status


class WakeOnLanController:
    def __init__(self, settings: Settings) -> None:
        self.configured = settings.wake_mac is not None
        self._state_file = Path(settings.wake_state_file) if settings.wake_state_file else None
        self._lock = threading.Lock()
        self._enabled = self.configured
        self._load()

    def status(self) -> dict[str, bool]:
        with self._lock:
            return {
                "configured": self.configured,
                "enabled": self._enabled,
            }

    def set_enabled(self, enabled: bool) -> None:
        if enabled and not self.configured:
            raise WakeOnLanError(
                "wake_mac_not_configured",
                HTTPStatus.CONFLICT,
            )
        with self._lock:
            self._persist(enabled)
            self._enabled = enabled

    def wake(self, settings: Settings) -> None:
        with self._lock:
            if not self.configured:
                raise WakeOnLanError("wake_mac_not_configured")
            if not self._enabled:
                raise WakeOnLanError(
                    "wake_on_lan_disabled",
                    HTTPStatus.FORBIDDEN,
                )
            _send_magic_packet(settings)

    def _load(self) -> None:
        if not self.configured or self._state_file is None:
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Could not read PISTATS_WAKE_STATE_FILE") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"enabled"}
            or not isinstance(payload["enabled"], bool)
        ):
            raise ValueError("PISTATS_WAKE_STATE_FILE contains invalid data")
        self._enabled = payload["enabled"]

    def _persist(self, enabled: bool) -> None:
        if self._state_file is None:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_file.with_name(f".{self._state_file.name}.tmp")
        with temporary.open("w", encoding="utf-8") as output:
            json.dump({"enabled": enabled}, output, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self._state_file)
        directory = os.open(
            self._state_file.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _tokens_equal(received: str, expected: str) -> bool:
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8"))


def _parse_nonnegative_int(value: str, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise MediaBackupError(400, f"invalid_{name}") from exc
    if parsed < 0:
        raise MediaBackupError(400, f"invalid_{name}")
    return parsed


def _parse_service_selection(query: str) -> tuple[str, ...] | None:
    parameters = parse_qs(query, keep_blank_values=True)
    values = parameters.get("services")
    if values is None:
        return None
    if len(values) != 1 or len(values[0]) > 12_800:
        raise ValueError("invalid services")
    if not values[0]:
        return ()

    names = tuple(dict.fromkeys(values[0].split(",")))
    if len(names) > 100 or any(
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name)
        for name in names
    ):
        raise ValueError("invalid services")
    return names


def _send_magic_packet(settings: Settings) -> None:
    if not settings.wake_mac:
        raise WakeOnLanError("wake_mac_not_configured")

    normalized_mac = re.sub(r"[^0-9A-Fa-f]", "", settings.wake_mac)
    if len(normalized_mac) != 12:
        raise WakeOnLanError("invalid_wake_mac")

    mac_bytes = bytes.fromhex(normalized_mac)
    packet = b"\xff" * 6 + mac_bytes * 16

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (settings.wake_broadcast, settings.wake_port))


def _transaction_request_id(value: str | None) -> str:
    if value:
        try:
            return str(uuid.UUID(value))
        except (ValueError, AttributeError):
            pass
    return str(uuid.uuid4())


def _log_transaction_sync(
    request_id: str,
    outcome: str,
    code: str | None = None,
) -> None:
    safe_code = (
        code
        if code is not None and re.fullmatch(r"[a-z0-9_]{1,64}", code)
        else "none" if code is None else "invalid_error_code"
    )
    transaction_logger.info(
        "transaction_sync request_id=%s outcome=%s code=%s",
        request_id,
        outcome,
        safe_code,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = load_settings()
    handler = create_handler(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(
        "OwnNode Agent listening on "
        f"http://{settings.host}:{settings.port} "
        f"(bind_mode={settings.bind_mode})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        handler.close_services()


if __name__ == "__main__":
    main()
