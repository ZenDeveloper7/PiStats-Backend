from __future__ import annotations

import hmac
import json
import re
import socket
import sqlite3
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


def create_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    collector = StatsCollector(settings)
    media_backup = MediaBackupService(settings) if settings.media_backup_root else None

    class PiStatsHandler(BaseHTTPRequestHandler):
        server_version = "PiStats/1.0"

        def do_GET(self) -> None:  # noqa: N802
            if not self._is_authorized():
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                )
                return

            if self.path == "/api/health":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "api_version": 1,
                        "status": "ok",
                        "features": {
                            "stats": True,
                            "wakeonlan": settings.wake_mac is not None,
                            "media_backup": media_backup is not None,
                            "backup_drive": bool(
                                settings.backup_label or settings.backup_mountpoint
                            ),
                            "docker_services": bool(settings.services),
                        },
                    },
                )
                return

            if self.path == "/api/stats":
                self._send_json(HTTPStatus.OK, collector.collect_all())
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if not self._is_authorized(
                allow_wake_token=path == "/api/wakeonlan/wake"
            ):
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                )
                return

            if path == "/api/wakeonlan/wake":
                try:
                    _send_magic_packet(settings)
                except WakeOnLanError as exc:
                    self._send_json(
                        HTTPStatus.BAD_REQUEST,
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

        def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        @classmethod
        def close_services(cls) -> None:
            if media_backup is not None:
                media_backup.close()

    return PiStatsHandler


class WakeOnLanError(Exception):
    pass


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


def main() -> None:
    settings = load_settings()
    handler = create_handler(settings)
    server = ThreadingHTTPServer((settings.host, settings.port), handler)
    print(
        "PiStats backend listening on "
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
