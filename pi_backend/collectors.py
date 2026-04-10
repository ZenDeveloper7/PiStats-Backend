from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings


THERMAL_ZONE_ROOT = Path("/sys/class/thermal")


@dataclass
class CpuSample:
    total: int
    idle: int


class StatsCollector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._last_cpu_sample: CpuSample | None = None
        self._last_cpu_timestamp: float | None = None

    def collect_all(self) -> dict[str, Any]:
        return {
            "host": socket.gethostname(),
            "uptime_seconds": self.read_uptime_seconds(),
            "cpu_percent": self.read_cpu_percent(),
            "memory": self.read_memory(),
            "disk": self.read_root_disk(),
            "temperature_c": self.read_temperature_c(),
            "load_average": self.read_load_average(),
            "backup_drive": self.read_backup_drive(),
            "services": self.read_services(),
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }

    def read_uptime_seconds(self) -> int:
        try:
            return int(float(Path("/proc/uptime").read_text().split()[0]))
        except (FileNotFoundError, ValueError, IndexError):
            return 0

    def read_cpu_percent(self) -> float:
        current = self._read_cpu_sample()
        previous = self._last_cpu_sample
        if previous is None:
            previous = current
            time.sleep(0.15)
            current = self._read_cpu_sample()

        self._last_cpu_sample = current
        self._last_cpu_timestamp = time.time()

        total_delta = current.total - previous.total
        idle_delta = current.idle - previous.idle
        if total_delta <= 0:
            return 0.0
        busy = total_delta - idle_delta
        return round(max(0.0, min(100.0, busy * 100.0 / total_delta)), 1)

    def read_memory(self) -> dict[str, int]:
        values: dict[str, int] = {}
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                parts = line.replace(":", "").split()
                if len(parts) >= 2:
                    values[parts[0]] = int(parts[1])
        except (FileNotFoundError, ValueError):
            pass

        total_kb = values.get("MemTotal", 0)
        available_kb = values.get("MemAvailable", 0)
        used_kb = max(total_kb - available_kb, 0)
        return {
            "used_mb": round(used_kb / 1024),
            "total_mb": round(total_kb / 1024),
        }

    def read_root_disk(self) -> dict[str, float]:
        stats = os.statvfs("/")
        total_bytes = stats.f_blocks * stats.f_frsize
        available_bytes = stats.f_bavail * stats.f_frsize
        used_bytes = max(total_bytes - available_bytes, 0)
        total_gb = round(total_bytes / (1024 ** 3), 1)
        used_gb = round(used_bytes / (1024 ** 3), 1)
        used_percent = round((used_bytes / total_bytes) * 100, 1) if total_bytes else 0.0
        return {
            "root_used_gb": used_gb,
            "root_total_gb": total_gb,
            "root_used_percent": used_percent,
        }

    def read_temperature_c(self) -> float | None:
        candidates = sorted(THERMAL_ZONE_ROOT.glob("thermal_zone*/temp"))
        for candidate in candidates:
            try:
                raw = candidate.read_text().strip()
                value = float(raw)
                return round(value / 1000.0 if value > 1000 else value, 1)
            except (FileNotFoundError, ValueError):
                continue
        return None

    def read_load_average(self) -> list[float]:
        try:
            parts = Path("/proc/loadavg").read_text().split()
            return [round(float(parts[index]), 2) for index in range(3)]
        except (FileNotFoundError, ValueError, IndexError):
            return [0.0, 0.0, 0.0]

    def read_services(self) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for name in self.settings.services:
            status, detail = self._read_docker_service_status(name)
            results.append(
                {
                    "name": name,
                    "status": status,
                    "detail": detail,
                }
            )
        return results

    def read_backup_drive(self) -> dict[str, Any]:
        lsblk_info = self._read_lsblk()
        preferred = self._find_preferred_backup(lsblk_info)
        mounted = False
        mountpoint = None
        device = None
        label = self.settings.backup_label
        connected = preferred is not None

        if preferred is not None:
            device = preferred.get("path")
            label = preferred.get("label") or label
            mountpoint = preferred.get("mountpoint")
            mounted = bool(mountpoint)

        if self.settings.backup_mountpoint and not mounted:
            resolved_mount = self._find_mount(self.settings.backup_mountpoint)
            if resolved_mount is not None:
                mounted = True
                mountpoint = resolved_mount["target"]
                device = resolved_mount.get("source") or device
                connected = True

        return {
            "connected": connected,
            "mounted": mounted,
            "label": label,
            "device": device,
            "mountpoint": mountpoint,
        }

    def _read_cpu_sample(self) -> CpuSample:
        line = Path("/proc/stat").read_text().splitlines()[0]
        values = [int(part) for part in line.split()[1:]]
        idle = values[3] + values[4]
        total = sum(values)
        return CpuSample(total=total, idle=idle)

    def _read_docker_service_status(self, name: str) -> tuple[str, str]:
        inspect = self._run_command(
            [
                "docker",
                "inspect",
                name,
                "--format",
                "{{.State.Status}}",
            ]
        )
        if inspect is None:
            return "unknown", "docker unavailable or container missing"

        status = inspect.strip().lower()
        normalized = {
            "running": "up",
            "exited": "down",
            "dead": "down",
            "created": "starting",
            "restarting": "starting",
            "paused": "degraded",
        }.get(status, status or "unknown")
        return normalized, status or "unknown"

    def _read_lsblk(self) -> list[dict[str, Any]]:
        output = self._run_command(
            [
                "lsblk",
                "-J",
                "-o",
                "NAME,PATH,LABEL,FSTYPE,MOUNTPOINT,TYPE",
            ]
        )
        if output is None:
            return []
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            return []

        devices: list[dict[str, Any]] = []
        for block in payload.get("blockdevices", []):
            devices.extend(self._flatten_lsblk_node(block))
        return devices

    def _flatten_lsblk_node(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        current = [
            {
                "name": node.get("name"),
                "path": node.get("path"),
                "label": node.get("label"),
                "fstype": node.get("fstype"),
                "mountpoint": node.get("mountpoint"),
                "type": node.get("type"),
            }
        ]
        for child in node.get("children", []):
            current.extend(self._flatten_lsblk_node(child))
        return current

    def _find_preferred_backup(self, devices: list[dict[str, Any]]) -> dict[str, Any] | None:
        if self.settings.backup_label:
            for device in devices:
                if (device.get("label") or "").lower() == self.settings.backup_label.lower():
                    return device

        exfat_devices = [
            device
            for device in devices
            if (device.get("fstype") or "").lower() == "exfat"
        ]
        return exfat_devices[0] if exfat_devices else None

    def _find_mount(self, mountpoint: str) -> dict[str, str] | None:
        output = self._run_command(["findmnt", "-J", mountpoint])
        if output is None:
            return None
        try:
            payload = json.loads(output)
            filesystems = payload.get("filesystems", [])
            if not filesystems:
                return None
            current = filesystems[0]
            return {
                "target": current.get("target"),
                "source": current.get("source"),
            }
        except (json.JSONDecodeError, AttributeError):
            return None

    def _run_command(self, args: list[str]) -> str | None:
        try:
            result = subprocess.run(
                args,
                check=False,
                capture_output=True,
                text=True,
                timeout=4,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None

        if result.returncode != 0:
            return None
        return result.stdout
