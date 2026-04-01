"""Execution runners for benchmarks."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)


@dataclass
class RunnerMetrics:
    wall_clock: float
    cpu_user: float
    cpu_system: float
    max_resident_set_kb: int

    def bytes_per_second(self, total_bytes: int) -> float:
        return total_bytes / self.wall_clock if self.wall_clock else 0.0


class Runner:
    name: str

    def run(
        self, command: Sequence[str], env: Mapping[str, str], timeout: int | None
    ) -> RunnerMetrics:
        raise NotImplementedError


class TimeRunner(Runner):
    name = "time"

    def __init__(self) -> None:
        time_bin = shutil.which("time") or "/usr/bin/time"
        self.time_bin = time_bin

    def run(
        self, command: Sequence[str], env: Mapping[str, str], timeout: int | None
    ) -> RunnerMetrics:
        fmt = "elapsed=%e\nuser=%U\nsystem=%S\nmaxrss=%M"
        with tempfile.NamedTemporaryFile(delete=False) as metrics_file:
            metrics_path = metrics_file.name
        try:
            full_cmd = [self.time_bin, "-f", fmt, "-o", metrics_path, *command]
            _LOG.debug("Executing: %s", full_cmd)
            try:
                subprocess.run(
                    full_cmd,
                    check=True,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(exc.stderr.strip() or str(exc)) from exc
            metrics = _parse_time_metrics(metrics_path)
            return RunnerMetrics(
                wall_clock=float(metrics["elapsed"]),
                cpu_user=float(metrics["user"]),
                cpu_system=float(metrics["system"]),
                max_resident_set_kb=int(metrics["maxrss"]),
            )
        finally:
            Path(metrics_path).unlink(missing_ok=True)


class RunnerRegistry:
    def __init__(self, runners: Iterable[Runner] | None = None) -> None:
        self._runners: dict[str, Runner] = {}
        if runners:
            for runner in runners:
                self.register(runner)
        else:
            self.register(TimeRunner())

    def register(self, runner: Runner) -> None:
        self._runners[runner.name] = runner

    def get(self, name: str) -> Runner:
        if name not in self._runners:
            raise ValueError(f"Unknown runner '{name}'")
        return self._runners[name]


def _parse_time_metrics(path: str) -> dict[str, str]:
    metrics: dict[str, str] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if "=" not in line:
                continue
            key, value = line.strip().split("=", 1)
            metrics[key] = value
    return metrics
