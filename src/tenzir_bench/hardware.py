"""Hardware/environment helpers for benchmark reports and reference matching."""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
from typing import Any


def environment_snapshot() -> dict[str, Any]:
    cpu_family = _cpu_family()
    arch = platform.machine().lower() or "unknown"
    cores = os.cpu_count()
    runner_class = os.getenv("TENZIR_BENCH_RUNNER_CLASS")
    return {
        "hostname": socket.gethostname(),
        "os": {
            "name": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "hardware": {
            "runner_class": runner_class,
            "architecture": arch,
            "cpu_family": cpu_family,
            "cores": cores,
        },
    }


def current_hardware_key() -> str:
    return hardware_key(environment_snapshot())


def hardware_key(snapshot: dict[str, Any]) -> str:
    hardware = snapshot.get("hardware", {})
    runner_class = _slug(hardware.get("runner_class") or "local")
    arch = _slug(hardware.get("architecture") or "unknown")
    cpu_family = _slug(hardware.get("cpu_family") or "unknown")
    cores = hardware.get("cores")
    cores_str = f"{cores}c" if isinstance(cores, int) and cores > 0 else "unknown"
    return "_".join((runner_class, arch, cpu_family, cores_str))


def _cpu_family() -> str:
    for value in (
        _linux_cpu_model(),
        _sysctl_value("machdep.cpu.brand_string"),
        platform.processor(),
    ):
        if value:
            return value
    return "unknown"


def _linux_cpu_model() -> str | None:
    cpuinfo = "/proc/cpuinfo"
    if not os.path.exists(cpuinfo):
        return None
    try:
        with open(cpuinfo, encoding="utf-8") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if key.strip().lower() == "model name":
                    return value.strip()
    except OSError:
        return None
    return None


def _sysctl_value(key: str) -> str | None:
    try:
        result = subprocess.run(
            ["sysctl", "-n", key],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _slug(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "unknown"
