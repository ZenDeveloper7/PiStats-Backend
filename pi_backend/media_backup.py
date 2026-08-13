from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from .config import Settings


MIME_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/gif": (".gif",),
    "image/webp": (".webp",),
    "image/heic": (".heic",),
    "image/heif": (".heif",),
    "image/avif": (".avif",),
    "image/bmp": (".bmp",),
    "image/tiff": (".tif", ".tiff"),
    "image/x-adobe-dng": (".dng",),
    "video/mp4": (".mp4",),
    "video/quicktime": (".mov",),
    "video/x-matroska": (".mkv",),
    "video/webm": (".webm",),
    "video/3gpp": (".3gp",),
    "video/3gpp2": (".3g2",),
    "video/mpeg": (".mpeg", ".mpg"),
    "video/x-msvideo": (".avi",),
    "video/x-ms-wmv": (".wmv",),
    "video/ogg": (".ogv",),
}


class MediaBackupError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class UploadRequest:
    display_name: str
    mime_type: str
    size_bytes: int
    modified_at_seconds: int
    captured_at_millis: int | None
    relative_path: str | None
    device_id: str
    media_key: str
    idempotency_key: str


class MediaBackupService:
    def __init__(self, settings: Settings) -> None:
        if not settings.media_backup_root:
            raise ValueError("media backup root is not configured")

        self.root = Path(settings.media_backup_root).expanduser().resolve()
        root_key = hashlib.sha256(os.fsencode(self.root)).hexdigest()[:16]
        state_dir = self.root.parent / ".pistats-media-state" / root_key
        self.temp_dir = Path(
            settings.media_backup_temp_dir or state_dir / "tmp"
        ).expanduser().resolve()
        self.database = Path(
            settings.media_backup_database or state_dir / "uploads.sqlite3"
        ).expanduser().resolve()
        self.max_bytes = settings.media_backup_max_bytes
        self.temp_max_age_seconds = settings.media_backup_temp_max_age_seconds
        self._cleanup_lock = threading.Lock()
        self._active_uploads_lock = threading.Lock()
        self._active_uploads: dict[Path, str] = {}
        self._next_cleanup_at = 0.0
        self._cleanup_interval_seconds = min(3600, self.temp_max_age_seconds)
        self._cleanup_stop = threading.Event()

        if not self.root.is_dir():
            raise ValueError("media backup root must be an existing directory")
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        if os.stat(self.root).st_dev != os.stat(self.temp_dir).st_dev:
            raise ValueError(
                "media backup temp directory must be on the same filesystem as the root"
            )
        if self.temp_dir == self.root or self.root in self.temp_dir.parents:
            raise ValueError(
                "media backup temp directory must be outside the shared library"
            )

        self._initialize_database()
        self._recover_pending_uploads()
        self.cleanup_stale_uploads()
        self._next_cleanup_at = time.monotonic() + self._cleanup_interval_seconds
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="pistats-media-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def close(self) -> None:
        self._cleanup_stop.set()
        self._cleanup_thread.join(timeout=5)

    def _cleanup_loop(self) -> None:
        while not self._cleanup_stop.wait(self._cleanup_interval_seconds):
            try:
                self.cleanup_if_due()
            except (OSError, sqlite3.Error):
                # A transient filesystem or database failure must not permanently
                # disable future scheduled cleanup attempts.
                pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS media_uploads (
                    idempotency_key TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    media_key TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'completed')),
                    size_bytes INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER
                )
                """
            )

    def _recover_pending_uploads(self) -> None:
        """Resolve reservations left behind by a stopped server process."""
        with self._connect() as connection:
            pending = connection.execute(
                """
                SELECT idempotency_key, destination FROM media_uploads
                WHERE state = 'pending'
                """
            ).fetchall()
            for idempotency_key, relative_destination in pending:
                if (self.root / relative_destination).is_file():
                    connection.execute(
                        """
                        UPDATE media_uploads SET state = 'completed', completed_at = ?
                        WHERE idempotency_key = ?
                        """,
                        (int(time.time()), idempotency_key),
                    )
                else:
                    connection.execute(
                        "DELETE FROM media_uploads WHERE idempotency_key = ?",
                        (idempotency_key,),
                    )

    def cleanup_if_due(self) -> None:
        now = time.monotonic()
        if now < self._next_cleanup_at or not self._cleanup_lock.acquire(blocking=False):
            return
        try:
            if now >= self._next_cleanup_at:
                self.cleanup_stale_uploads()
                self._next_cleanup_at = now + self._cleanup_interval_seconds
        finally:
            self._cleanup_lock.release()

    def cleanup_stale_uploads(self) -> None:
        cutoff = time.time() - self.temp_max_age_seconds
        with self._active_uploads_lock:
            active_paths = set(self._active_uploads)
            active_keys = set(self._active_uploads.values())
        for entry in self.temp_dir.glob("*.upload"):
            try:
                if (
                    entry not in active_paths
                    and entry.is_file()
                    and entry.stat().st_mtime < cutoff
                ):
                    entry.unlink()
            except FileNotFoundError:
                pass

        with self._connect() as connection:
            pending = connection.execute(
                """
                SELECT idempotency_key, destination FROM media_uploads
                WHERE state = 'pending' AND created_at < ?
                """,
                (int(cutoff),),
            ).fetchall()
            for idempotency_key, relative_destination in pending:
                if idempotency_key in active_keys:
                    continue
                if (self.root / relative_destination).is_file():
                    connection.execute(
                        """
                        UPDATE media_uploads SET state = 'completed', completed_at = ?
                        WHERE idempotency_key = ?
                        """,
                        (int(time.time()), idempotency_key),
                    )
                else:
                    connection.execute(
                        "DELETE FROM media_uploads WHERE idempotency_key = ?",
                        (idempotency_key,),
                    )

    def store(self, request: UploadRequest, body: BinaryIO) -> Path:
        self.cleanup_if_due()
        if request.size_bytes < 0:
            raise MediaBackupError(400, "invalid_size_bytes")
        if request.size_bytes > self.max_bytes:
            raise MediaBackupError(413, "file_too_large")
        if request.mime_type not in MIME_EXTENSIONS:
            raise MediaBackupError(415, "unsupported_media_type")
        extension = _validated_extension(request.display_name, request.mime_type)
        captured_seconds = (
            request.captured_at_millis // 1000
            if request.captured_at_millis is not None
            else request.modified_at_seconds
        )
        try:
            captured = datetime.fromtimestamp(captured_seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise MediaBackupError(400, "invalid_capture_time") from exc

        relative_destination = Path(
            request.device_id,
            f"{captured.year:04d}",
            f"{captured.month:02d}",
            f"{uuid.uuid4().hex}{extension}",
        )
        destination = self.root / relative_destination
        reservation = self._reserve(request, relative_destination)
        if reservation == "completed":
            raise MediaBackupError(409, "already_stored")
        if reservation == "pending":
            raise MediaBackupError(503, "upload_in_progress")

        temp_path = self.temp_dir / f"{uuid.uuid4().hex}.upload"
        with self._active_uploads_lock:
            self._active_uploads[temp_path] = request.idempotency_key
        try:
            received = self._stream_body(body, temp_path, request.size_bytes)
            if received != request.size_bytes:
                raise MediaBackupError(400, "content_length_mismatch")

            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, destination)
            _fsync_directory(destination.parent)
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE media_uploads
                    SET state = 'completed', completed_at = ?
                    WHERE idempotency_key = ?
                    """,
                    (int(time.time()), request.idempotency_key),
                )
            return destination
        except BaseException:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            if not destination.exists():
                with self._connect() as connection:
                    connection.execute(
                        "DELETE FROM media_uploads WHERE idempotency_key = ? AND state = 'pending'",
                        (request.idempotency_key,),
                    )
            raise
        finally:
            with self._active_uploads_lock:
                self._active_uploads.pop(temp_path, None)

    def _reserve(self, request: UploadRequest, destination: Path) -> str:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO media_uploads (
                        idempotency_key, device_id, media_key, destination,
                        state, size_bytes, created_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        request.idempotency_key,
                        request.device_id,
                        request.media_key,
                        str(destination),
                        request.size_bytes,
                        int(time.time()),
                    ),
                )
            return "reserved"
        except sqlite3.IntegrityError:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT state, destination FROM media_uploads WHERE idempotency_key = ?",
                    (request.idempotency_key,),
                ).fetchone()
                if row and row[0] == "pending" and (self.root / row[1]).is_file():
                    connection.execute(
                        """
                        UPDATE media_uploads SET state = 'completed', completed_at = ?
                        WHERE idempotency_key = ?
                        """,
                        (int(time.time()), request.idempotency_key),
                    )
                    return "completed"
            return row[0] if row else "pending"

    @staticmethod
    def _stream_body(body: BinaryIO, temp_path: Path, expected: int) -> int:
        received = 0
        with temp_path.open("xb") as output:
            while received < expected:
                chunk = body.read(min(1024 * 1024, expected - received))
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
            output.flush()
            os.fsync(output.fileno())
        return received


def _validated_extension(display_name: str, mime_type: str) -> str:
    allowed = MIME_EXTENSIONS[mime_type]
    candidate = Path(display_name.replace("\\", "/")).name
    suffix = Path(candidate).suffix.lower()
    return suffix if suffix in allowed else allowed[0]


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
