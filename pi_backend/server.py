from __future__ import annotations

import json
import re
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .collectors import StatsCollector
from .config import Settings, load_settings


def create_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    collector = StatsCollector(settings)

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
                        "status": "ok",
                        "features": {
                            "stats": True,
                            "wakeonlan": settings.wake_mac is not None,
                        },
                    },
                )
                return

            if self.path == "/api/stats":
                self._send_json(HTTPStatus.OK, collector.collect_all())
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._is_authorized():
                self._send_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "unauthorized"},
                )
                return

            if self.path == "/api/wakeonlan/wake":
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

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _is_authorized(self) -> bool:
            if settings.dev_mode:
                return True
            auth_header = self.headers.get("Authorization", "")
            wake_token_header = self.headers.get("X-Wake-Token", "")
            expected = f"Bearer {settings.token}"
            return bool(settings.token) and (
                auth_header == expected or wake_token_header == settings.token
            )

        def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return PiStatsHandler


class WakeOnLanError(Exception):
    pass


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


if __name__ == "__main__":
    main()
