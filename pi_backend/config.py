from __future__ import annotations

import json
import ipaddress
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


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
    wake_state_file: str | None = None
    media_backup_root: str | None = None
    media_backup_database: str | None = None
    media_backup_temp_dir: str | None = None
    media_backup_max_bytes: int = 1_073_741_824
    media_backup_temp_max_age_seconds: int = 86_400
    media_backup_read_timeout_seconds: int = 300
    transaction_database: str | None = None
    actual_server_url: str | None = None
    actual_password: str | None = None
    actual_sync_id: str | None = None
    actual_currency: str | None = None
    actual_encryption_password: str | None = None
    actual_data_dir: str | None = None
    actual_api_module: str = "@actual-app/api"
    actual_mappings_file: str | None = None
    actual_bridge_command: tuple[str, ...] = ()
    actual_timeout_seconds: int = 60
    actual_health_cache_seconds: int = 30

    @property
    def actual_budget_configured(self) -> bool:
        return all(
            (
                self.actual_server_url,
                self.actual_password,
                self.actual_sync_id,
                self.actual_currency,
                self.actual_mappings_file,
            )
        )


def load_settings() -> Settings:
    bind_mode = os.getenv("PISTATS_BIND_MODE", "localhost").strip().lower() or "localhost"
    services = tuple(
        value.strip()
        for value in os.getenv("PISTATS_SERVICES", "").split(",")
        if value.strip()
    )
    default_bridge = ("node", str(Path(__file__).with_name("actual_bridge.cjs")))
    settings = Settings(
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
        wake_state_file=_clean_env("PISTATS_WAKE_STATE_FILE"),
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
        transaction_database=_clean_env("PISTATS_TRANSACTION_DATABASE"),
        actual_server_url=_clean_env("PISTATS_ACTUAL_SERVER_URL"),
        actual_password=_opaque_env("PISTATS_ACTUAL_PASSWORD"),
        actual_sync_id=_clean_env("PISTATS_ACTUAL_SYNC_ID"),
        actual_currency=_currency_env("PISTATS_ACTUAL_CURRENCY"),
        actual_encryption_password=_opaque_env(
            "PISTATS_ACTUAL_ENCRYPTION_PASSWORD"
        ),
        actual_data_dir=_clean_env("PISTATS_ACTUAL_DATA_DIR"),
        actual_api_module=(
            _clean_env("PISTATS_ACTUAL_API_MODULE") or "@actual-app/api"
        ),
        actual_mappings_file=_clean_env("PISTATS_ACTUAL_MAPPINGS_FILE"),
        actual_bridge_command=_command_env(
            "PISTATS_ACTUAL_BRIDGE_COMMAND", default_bridge
        ),
        actual_timeout_seconds=_positive_int_env(
            "PISTATS_ACTUAL_TIMEOUT_SECONDS", 60
        ),
        actual_health_cache_seconds=_positive_int_env(
            "PISTATS_ACTUAL_HEALTH_CACHE_SECONDS", 30
        ),
    )
    _validate_settings(settings)
    return settings


def _validate_settings(settings: Settings) -> None:
    if settings.bind_mode not in {"localhost", "tailscale", "custom"}:
        raise ValueError("PISTATS_BIND_MODE must be localhost, tailscale, or custom")
    if settings.port not in range(1, 65_536):
        raise ValueError("PISTATS_PORT must be between 1 and 65535")
    if not settings.dev_mode and not settings.token:
        raise ValueError("PISTATS_TOKEN is required unless PISTATS_DEV_MODE=1")
    if settings.dev_mode and settings.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("PISTATS_DEV_MODE may only bind to localhost")
    if settings.wake_port not in range(1, 65_536):
        raise ValueError("PISTATS_WAKE_PORT must be between 1 and 65535")
    if settings.wake_mac:
        normalized_mac = re.sub(r"[^0-9A-Fa-f]", "", settings.wake_mac)
        if len(normalized_mac) != 12:
            raise ValueError("PISTATS_WAKE_MAC must contain exactly 12 hexadecimal digits")
        try:
            broadcast = ipaddress.ip_address(settings.wake_broadcast)
        except ValueError as exc:
            raise ValueError("PISTATS_WAKE_BROADCAST must be a valid IPv4 address") from exc
        if broadcast.version != 4:
            raise ValueError("PISTATS_WAKE_BROADCAST must be an IPv4 address")

    actual_values = {
        "PISTATS_ACTUAL_SERVER_URL": settings.actual_server_url,
        "PISTATS_ACTUAL_PASSWORD": settings.actual_password,
        "PISTATS_ACTUAL_SYNC_ID": settings.actual_sync_id,
        "PISTATS_ACTUAL_CURRENCY": settings.actual_currency,
        "PISTATS_ACTUAL_MAPPINGS_FILE": settings.actual_mappings_file,
    }
    configured_actual_values = [key for key, value in actual_values.items() if value]
    if configured_actual_values and not settings.actual_budget_configured:
        missing = [key for key, value in actual_values.items() if not value]
        raise ValueError(
            "Actual Budget configuration is incomplete; missing " + ", ".join(missing)
        )
    if settings.actual_server_url:
        parsed_url = urlsplit(settings.actual_server_url)
        try:
            parsed_port = parsed_url.port
        except ValueError as exc:
            raise ValueError("PISTATS_ACTUAL_SERVER_URL has an invalid port") from exc
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError(
                "PISTATS_ACTUAL_SERVER_URL must be an http(s) URL without "
                "credentials, query, or fragment"
            )
        if parsed_port is not None and parsed_port not in range(1, 65_536):
            raise ValueError("PISTATS_ACTUAL_SERVER_URL has an invalid port")
    if settings.actual_currency and (
        len(settings.actual_currency) != 3
        or not settings.actual_currency.isascii()
        or not settings.actual_currency.isalpha()
        or settings.actual_currency != settings.actual_currency.upper()
    ):
        raise ValueError("PISTATS_ACTUAL_CURRENCY must be a three-letter currency code")
    if settings.actual_budget_configured and not settings.actual_bridge_command:
        raise ValueError("PISTATS_ACTUAL_BRIDGE_COMMAND must not be empty")


def _clean_env(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    return value or None


def _opaque_env(key: str) -> str | None:
    value = os.getenv(key)
    return value if value else None


def _currency_env(key: str) -> str | None:
    value = _clean_env(key)
    return value.upper() if value else None


def _positive_int_env(key: str, default: int) -> int:
    raw_value = os.getenv(key, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{key} must be greater than zero")
    return value


def _command_env(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(key)
    if raw_value is None:
        return default
    try:
        return tuple(shlex.split(raw_value))
    except ValueError as exc:
        raise ValueError(f"{key} is not a valid command") from exc


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
