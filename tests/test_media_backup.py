from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from dataclasses import replace
from http.server import ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from pi_backend.config import Settings
from pi_backend.media_backup import (
    MediaBackupError,
    MediaBackupService,
    UploadRequest,
)
from pi_backend.server import WakeOnLanController, create_handler


DEVICE_ID = "12345678-1234-5678-9234-567812345678"
TOKEN = "test-token"


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


class RunningServer:
    def __init__(self, settings: Settings) -> None:
        self.handler = create_handler(settings)
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

    def post(
        self,
        body: bytes,
        *,
        display_name: str = "IMG_0001.jpg",
        mime_type: str = "image/jpeg",
        size_bytes: int | None = None,
        token: str = TOKEN,
        idempotency_key: str = "key-1",
        use_wake_header: bool = False,
    ) -> tuple[int, bytes]:
        size = len(body) if size_bytes is None else size_bytes
        path = (
            "/api/media/backup/items"
            f"?display_name={display_name}"
            f"&mime_type={mime_type.replace('/', '%2F')}"
            f"&size_bytes={size}"
            "&modified_at_seconds=1700000000"
            "&relative_path=..%2F..%2Fetc%2F"
        )
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        headers = {
            "Content-Type": mime_type,
            "Content-Length": str(len(body)),
            "Idempotency-Key": idempotency_key,
            "X-PiStats-Device-Id": DEVICE_ID,
            "X-PiStats-Media-Key": "external:42:1700000000",
        }
        if use_wake_header:
            headers["X-Wake-Token"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(
            "POST",
            path,
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def get(self, path: str, *, token: str = TOKEN) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "GET",
            path,
            headers={"Authorization": f"Bearer {token}"},
        )
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def put_json(
        self,
        path: str,
        payload: object,
        *,
        token: str = TOKEN,
    ) -> tuple[int, bytes]:
        body = json.dumps(payload).encode("utf-8")
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "PUT",
            path,
            body=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def post_wake(self, *, token: str = TOKEN) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        connection.request(
            "POST",
            "/api/wakeonlan/wake",
            headers={"Authorization": f"Bearer {token}"},
        )
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result


class BlockingBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.entered = threading.Event()
        self.release = threading.Event()

    def read(self, size: int = -1) -> bytes:
        self.entered.set()
        self.release.wait(timeout=3)
        payload, self.payload = self.payload[:size], self.payload[size:]
        return payload


class MediaBackupApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "library"
        self.root.mkdir()
        self.settings = base_settings(
            media_backup_root=str(self.root),
            media_backup_max_bytes=16,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_stores_file_and_returns_conflict_for_repeat(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(b"jpeg-data")
            self.assertEqual(status, 201)
            status, _ = server.post(b"jpeg-data")
            self.assertEqual(status, 409)

        stored = list(self.root.rglob("*.*"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].read_bytes(), b"jpeg-data")
        self.assertEqual(stored[0].suffix, ".jpg")
        self.assertEqual(
            stored[0].relative_to(self.root).parts[:3],
            (DEVICE_ID, "2023", "11"),
        )

        database = next(
            (self.base / ".pistats-media-state").glob("*/uploads.sqlite3")
        )
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT state, size_bytes FROM media_uploads WHERE idempotency_key = 'key-1'"
            ).fetchone()
        self.assertEqual(row, ("completed", 9))

    def test_health_advertises_service_discovery(self) -> None:
        with RunningServer(self.settings) as server:
            status, body = server.get("/api/health")

        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(
            payload["features"],
            {
                "stats": True,
                "wakeonlan": False,
                "wakeonlan_control": True,
                "media_backup": True,
                "backup_drive": False,
                "docker_services": True,
                "service_selection": True,
            },
        )


    def test_lists_services_and_filters_stats_using_app_selection(self) -> None:
        available = [
            {"name": "photos", "status": "up", "detail": "Up 2 hours"},
            {"name": "samba", "status": "down", "detail": "Exited (0)"},
        ]
        with patch("pi_backend.collectors.StatsCollector.list_services", return_value=available):
            with RunningServer(self.settings) as server:
                status, body = server.get("/api/services")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"services": available})

                status, body = server.get("/api/stats?services=samba")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["services"], [available[1]])

                status, body = server.get("/api/stats?services=")
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)["services"], [])

    def test_rejects_invalid_service_selection(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.get("/api/stats?services=bad%20name")
        self.assertEqual(status, 400)

    def test_uses_canonical_extension_when_name_does_not_match_mime(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(
                b"png",
                display_name="../../video.mp4",
                mime_type="image/png",
            )
        self.assertEqual(status, 201)
        stored = list(self.root.rglob("*.*"))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].suffix, ".png")

    def test_rejects_unauthorized_request(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(b"data", token="wrong")
        self.assertEqual(status, 401)
        self.assertEqual(list(self.root.rglob("*.*")), [])

    def test_wake_token_alias_cannot_authorize_media_uploads(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(b"data", use_wake_header=True)
        self.assertEqual(status, 401)
        self.assertEqual(list(self.root.rglob("*.*")), [])

    def test_rejects_oversized_request(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(b"x" * 17)
        self.assertEqual(status, 413)
        self.assertEqual(list(self.root.rglob("*.*")), [])

    def test_rejects_mismatched_content_length_metadata(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.post(b"data", size_bytes=5)
        self.assertEqual(status, 400)
        self.assertEqual(list(self.root.rglob("*.*")), [])

    def test_returns_not_found_when_feature_is_disabled(self) -> None:
        with RunningServer(base_settings()) as server:
            status, _ = server.post(b"data")
        self.assertEqual(status, 404)

    def test_short_body_removes_temporary_file_and_reservation(self) -> None:
        service = MediaBackupService(self.settings)
        upload = UploadRequest(
            display_name="image.jpg",
            mime_type="image/jpeg",
            size_bytes=5,
            modified_at_seconds=1700000000,
            captured_at_millis=None,
            relative_path=None,
            device_id=DEVICE_ID,
            media_key="external:42:1700000000",
            idempotency_key="short-body",
        )
        with self.assertRaisesRegex(MediaBackupError, "content_length_mismatch"):
            service.store(upload, BytesIO(b"four"))

        temp_dir = service.temp_dir
        self.assertEqual(list(temp_dir.glob("*.upload")), [])
        with closing(sqlite3.connect(service.database)) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM media_uploads WHERE idempotency_key = 'short-body'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

        service.close()

    def test_sibling_roots_use_independent_idempotency_databases(self) -> None:
        sibling_root = self.base / "second-library"
        sibling_root.mkdir()
        first = MediaBackupService(self.settings)
        second = MediaBackupService(
            replace(self.settings, media_backup_root=str(sibling_root))
        )
        try:
            self.assertNotEqual(first.database, second.database)
        finally:
            first.close()
            second.close()

    def test_removes_stale_temporary_files_while_idle(self) -> None:
        service = MediaBackupService(
            replace(self.settings, media_backup_temp_max_age_seconds=1)
        )
        try:
            stale = service.temp_dir / "stale.upload"
            stale.write_bytes(b"partial")
            old = time.time() - 10
            os.utime(stale, (old, old))
            deadline = time.monotonic() + 3
            while stale.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(stale.exists())
        finally:
            service.close()

    def test_stalled_upload_returns_request_timeout(self) -> None:
        settings = replace(self.settings, media_backup_read_timeout_seconds=1)
        with RunningServer(settings) as server:
            with socket.create_connection(server.server.server_address, timeout=2) as client:
                client.settimeout(3)
                request = (
                    "POST /api/media/backup/items?display_name=image.jpg"
                    "&mime_type=image%2Fjpeg&size_bytes=4"
                    "&modified_at_seconds=1700000000 HTTP/1.0\r\n"
                    f"Authorization: Bearer {TOKEN}\r\n"
                    "Content-Type: image/jpeg\r\n"
                    "Content-Length: 4\r\n"
                    "Idempotency-Key: stalled-upload\r\n"
                    f"X-PiStats-Device-Id: {DEVICE_ID}\r\n"
                    "X-PiStats-Media-Key: external:42:1700000000\r\n"
                    "\r\n"
                )
                client.sendall(request.encode("ascii"))
                response = client.recv(4096)

        self.assertIn(b" 408 ", response)
        self.assertEqual(list(self.root.rglob("*.*")), [])

    def test_scheduled_cleanup_does_not_remove_an_active_upload(self) -> None:
        service = MediaBackupService(
            replace(self.settings, media_backup_temp_max_age_seconds=1)
        )
        body = BlockingBody(b"data")
        upload = UploadRequest(
            display_name="image.jpg",
            mime_type="image/jpeg",
            size_bytes=4,
            modified_at_seconds=1700000000,
            captured_at_millis=None,
            relative_path=None,
            device_id=DEVICE_ID,
            media_key="external:active",
            idempotency_key="active-upload",
        )
        failures: list[BaseException] = []

        def run_upload() -> None:
            try:
                service.store(upload, body)
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=run_upload)
        thread.start()
        try:
            self.assertTrue(body.entered.wait(timeout=2))
            temp_path = next(service.temp_dir.glob("*.upload"))
            old = int(time.time()) - 10
            os.utime(temp_path, (old, old))
            with closing(sqlite3.connect(service.database)) as connection:
                connection.execute(
                    "UPDATE media_uploads SET created_at = ? WHERE idempotency_key = ?",
                    (old, upload.idempotency_key),
                )
            service.cleanup_stale_uploads()
            self.assertTrue(temp_path.exists())
            with closing(sqlite3.connect(service.database)) as connection:
                state = connection.execute(
                    "SELECT state FROM media_uploads WHERE idempotency_key = ?",
                    (upload.idempotency_key,),
                ).fetchone()
            self.assertEqual(state, ("pending",))
        finally:
            body.release.set()
            thread.join(timeout=3)
            service.close()

        self.assertEqual(failures, [])


class WakeOnLanControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.state_file = Path(self.temporary_directory.name) / "wake.json"
        self.settings = base_settings(
            wake_mac="00:11:22:33:44:55",
            wake_state_file=str(self.state_file),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_disables_wake_requests_and_persists_across_restart(self) -> None:
        with patch("pi_backend.server._send_magic_packet") as send_packet:
            with RunningServer(self.settings) as server:
                status, body = server.get("/api/wakeonlan/settings")
                self.assertEqual(status, 200)
                self.assertEqual(
                    json.loads(body),
                    {"configured": True, "enabled": True},
                )

                status, body = server.put_json(
                    "/api/wakeonlan/settings",
                    {"enabled": False},
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    json.loads(body),
                    {"configured": True, "enabled": False},
                )
                status, body = server.post_wake()
                self.assertEqual(status, 403)
                self.assertEqual(json.loads(body)["error"], "wake_on_lan_disabled")
                send_packet.assert_not_called()

            with RunningServer(self.settings) as server:
                status, body = server.get("/api/wakeonlan/settings")
                self.assertEqual(status, 200)
                self.assertFalse(json.loads(body)["enabled"])

    def test_enables_wake_requests(self) -> None:
        with patch("pi_backend.server._send_magic_packet") as send_packet:
            with RunningServer(self.settings) as server:
                status, _ = server.put_json(
                    "/api/wakeonlan/settings",
                    {"enabled": False},
                )
                self.assertEqual(status, 200)
                status, _ = server.put_json(
                    "/api/wakeonlan/settings",
                    {"enabled": True},
                )
                self.assertEqual(status, 200)
                status, _ = server.post_wake()
                self.assertEqual(status, 200)
                send_packet.assert_called_once_with(self.settings)

    def test_syncs_state_file_and_parent_directory(self) -> None:
        controller = WakeOnLanController(self.settings)
        with patch("pi_backend.server.os.fsync", wraps=os.fsync) as sync:
            controller.set_enabled(False)
        self.assertEqual(sync.call_count, 2)
        self.assertEqual(json.loads(self.state_file.read_text()), {"enabled": False})

    def test_rejects_unauthorized_or_invalid_settings_updates(self) -> None:
        with RunningServer(self.settings) as server:
            status, _ = server.put_json(
                "/api/wakeonlan/settings",
                {"enabled": False},
                token="wrong",
            )
            self.assertEqual(status, 401)
            status, body = server.put_json(
                "/api/wakeonlan/settings",
                {"enabled": "false"},
            )
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(body)["error"], "invalid_wake_settings")

    def test_cannot_enable_without_a_configured_mac(self) -> None:
        with RunningServer(base_settings(wake_state_file=str(self.state_file))) as server:
            status, body = server.put_json(
                "/api/wakeonlan/settings",
                {"enabled": True},
            )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)["error"], "wake_mac_not_configured")


if __name__ == "__main__":
    unittest.main()
