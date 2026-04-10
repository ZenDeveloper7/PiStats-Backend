from __future__ import annotations

import json
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
                self._send_json(HTTPStatus.OK, {"status": "ok"})
                return

            if self.path == "/api/stats":
                self._send_json(HTTPStatus.OK, collector.collect_all())
                return

            if self.path == "/api/services":
                self._send_json(HTTPStatus.OK, {"services": collector.read_services()})
                return

            if self.path == "/api/backup-status":
                self._send_json(
                    HTTPStatus.OK,
                    {"backup_drive": collector.read_backup_drive()},
                )
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _is_authorized(self) -> bool:
            if settings.dev_mode:
                return True
            auth_header = self.headers.get("Authorization", "")
            expected = f"Bearer {settings.token}"
            return bool(settings.token) and auth_header == expected

        def _send_json(self, status: HTTPStatus, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return PiStatsHandler


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
