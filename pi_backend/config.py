from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    token: str
    dev_mode: bool
    bind_mode: str
    services: tuple[str, ...]
    backup_label: str | None
    backup_mountpoint: str | None
    wake_mac: str | None
    wake_broadcast: str
    wake_port: int
    media_backup_root: str | None = None
    media_backup_database: str | None = None
    media_backup_temp_dir: str | None = None
    media_backup_max_bytes: int = 1_073_741_824
    media_backup_temp_max_age_seconds: int = 86_400
    media_backup_read_timeout_seconds: int = 300


def load_settings() -> Settings:
    bind_mode = os.getenv("PISTATS_BIND_MODE", "localhost").strip().lower() or "localhost"
    services = tuple(
        value.strip()
        for value in os.getenv("PISTATS_SERVICES", "").split(",")
        if value.strip()
    )
    return Settings(
        host=_resolve_host(bind_mode),
        port=int(os.getenv("PISTATS_PORT", "8787")),
        token=os.getenv("PISTATS_TOKEN", ""),
        dev_mode=os.getenv("PISTATS_DEV_MODE", "0") == "1",
        bind_mode=bind_mode,
        services=services,
        backup_label=_clean_env("PISTATS_BACKUP_LABEL"),
        backup_mountpoint=_clean_env("PISTATS_BACKUP_MOUNTPOINT"),
        wake_mac=_clean_env("PISTATS_WAKE_MAC"),
        wake_broadcast=os.getenv("PISTATS_WAKE_BROADCAST", "192.168.1.255").strip() or "192.168.1.255",
        wake_port=int(os.getenv("PISTATS_WAKE_PORT", "9")),
        media_backup_root=_clean_env("PISTATS_MEDIA_BACKUP_ROOT"),
        media_backup_database=_clean_env("PISTATS_MEDIA_BACKUP_DATABASE"),
        media_backup_temp_dir=_clean_env("PISTATS_MEDIA_BACKUP_TEMP_DIR"),
        media_backup_max_bytes=_positive_int_env(
            "PISTATS_MEDIA_BACKUP_MAX_BYTES", 1_073_741_824
        ),
        media_backup_temp_max_age_seconds=_positive_int_env(
            "PISTATS_MEDIA_BACKUP_TEMP_MAX_AGE_SECONDS", 86_400
        ),
        media_backup_read_timeout_seconds=_positive_int_env(
            "PISTATS_MEDIA_BACKUP_READ_TIMEOUT_SECONDS", 300
        ),
    )


def _clean_env(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    return value or None


def _positive_int_env(key: str, default: int) -> int:
    raw_value = os.getenv(key, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _resolve_host(bind_mode: str) -> str:
    explicit_host = _clean_env("PISTATS_HOST")
    if bind_mode == "custom":
        return explicit_host or "127.0.0.1"
    if bind_mode == "tailscale":
        return _resolve_tailscale_host()
    return explicit_host or "127.0.0.1"


def _resolve_tailscale_host() -> str:
    explicit_ip = _clean_env("PISTATS_TAILSCALE_IP")
    if explicit_ip:
        return explicit_ip

    try:
        result = subprocess.run(
            ["ip", "-4", "-j", "addr", "show", "dev", "tailscale0"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "PISTATS_BIND_MODE=tailscale requires the 'ip' command or PISTATS_TAILSCALE_IP"
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            "Could not inspect tailscale0. Is Tailscale running on the Pi?"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not parse tailscale0 address information") from exc

    for interface in payload:
        for address in interface.get("addr_info", []):
            local = (address.get("local") or "").strip()
            if local:
                return local

    raise RuntimeError(
        "No IPv4 address found on tailscale0. Set PISTATS_TAILSCALE_IP manually if needed."
    )
