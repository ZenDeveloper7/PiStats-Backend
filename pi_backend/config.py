from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass


DEFAULT_SERVICES = ("vaultwarden", "trilium", "samba", "pihole")


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


def load_settings() -> Settings:
    bind_mode = os.getenv("PISTATS_BIND_MODE", "localhost").strip().lower() or "localhost"
    services = tuple(
        value.strip()
        for value in os.getenv("PISTATS_SERVICES", ",".join(DEFAULT_SERVICES)).split(",")
        if value.strip()
    )
    return Settings(
        host=_resolve_host(bind_mode),
        port=int(os.getenv("PISTATS_PORT", "8787")),
        token=os.getenv("PISTATS_TOKEN", ""),
        dev_mode=os.getenv("PISTATS_DEV_MODE", "0") == "1",
        bind_mode=bind_mode,
        services=services or DEFAULT_SERVICES,
        backup_label=_clean_env("PISTATS_BACKUP_LABEL"),
        backup_mountpoint=_clean_env("PISTATS_BACKUP_MOUNTPOINT"),
        wake_mac=_clean_env("PISTATS_WAKE_MAC"),
        wake_broadcast=os.getenv("PISTATS_WAKE_BROADCAST", "192.168.1.255").strip() or "192.168.1.255",
        wake_port=int(os.getenv("PISTATS_WAKE_PORT", "9")),
    )


def _clean_env(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    return value or None


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
