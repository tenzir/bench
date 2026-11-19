"""Execution runners for benchmarks."""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast, override

from .runtime import TenzirRuntime

_LOG = logging.getLogger(__name__)
_DOCKER_MAXRSS_RE = re.compile(r"^tenzir-bench-maxrss=(?P<value>\d+)$", re.MULTILINE)


@dataclass
class RunnerMetrics:
    wall_clock: float
    cpu_user: float
    cpu_system: float
    max_resident_set_kb: int

    def bytes_per_second(self, total_bytes: int) -> float:
        return total_bytes / self.wall_clock if self.wall_clock else 0.0


class Runner:
    name: ClassVar[str]

    def command(
        self,
        runtime: TenzirRuntime,
        tenzir_args: Sequence[str],
        pipeline_path: Path,
    ) -> list[str]:
        """Return the command that runs the benchmark, which `run` measures."""
        return [*runtime.command_for_tenzir(tenzir_args), "--file", str(pipeline_path)]

    def run(
        self, command: Sequence[str], env: Mapping[str, str], timeout: int | None
    ) -> RunnerMetrics:
        del command, env, timeout
        raise NotImplementedError


class TimeRunner(Runner):
    name: ClassVar[str] = "time"

    def __init__(self) -> None:
        time_bin = shutil.which("time") or "/usr/bin/time"
        self.time_bin: str = time_bin

    @override
    def run(
        self, command: Sequence[str], env: Mapping[str, str], timeout: int | None
    ) -> RunnerMetrics:
        fmt = "elapsed=%e\nuser=%U\nsystem=%S\nmaxrss=%M"
        with tempfile.NamedTemporaryFile(delete=False) as metrics_file:
            metrics_path = metrics_file.name
        try:
            full_cmd = [self.time_bin, "-f", fmt, "-o", metrics_path, *command]
            _LOG.debug("Executing: %s", full_cmd)
            proc = subprocess.run(
                full_cmd,
                check=False,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or f"runner exited with {proc.returncode}")
            metrics = _parse_time_metrics(metrics_path)
            maxrss = _parse_docker_maxrss(proc.stderr) or int(metrics["maxrss"])
            return RunnerMetrics(
                wall_clock=float(metrics["elapsed"]),
                cpu_user=float(metrics["user"]),
                cpu_system=float(metrics["system"]),
                max_resident_set_kb=maxrss,
            )
        finally:
            Path(metrics_path).unlink(missing_ok=True)


class DeploymentRunner(Runner):
    """Runs the pipeline in a deployment instead of in a process of its own.

    The command starts a deployment for this one run, which measures itself:
    see `tenzir_bench.deployment` for what the measurements cover.
    """

    name: ClassVar[str] = "deployment"

    @override
    def command(
        self,
        runtime: TenzirRuntime,
        tenzir_args: Sequence[str],
        pipeline_path: Path,
    ) -> list[str]:
        if tenzir_args:
            raise RuntimeError(
                f"{pipeline_path}: a deployment takes no Tenzir options, "
                + f"got {shlex.join(tenzir_args)}"
            )
        return [
            sys.executable,
            "-m",
            "tenzir_bench.deployment",
            "--tenzir",
            str(runtime.tenzir_path),
            "--file",
            str(pipeline_path),
        ]

    @override
    def run(
        self, command: Sequence[str], env: Mapping[str, str], timeout: int | None
    ) -> RunnerMetrics:
        # The command enforces the timeout itself, because only it can stop the
        # deployment it started.
        full_cmd = [*command, *(() if timeout is None else ("--timeout", str(timeout)))]
        _LOG.debug("Executing: %s", full_cmd)
        proc = subprocess.run(full_cmd, check=False, env=env, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or f"runner exited with {proc.returncode}")
        metrics = cast(dict[str, float], json.loads(proc.stdout))
        return RunnerMetrics(
            wall_clock=float(metrics["wall_clock"]),
            cpu_user=float(metrics["cpu_user"]),
            cpu_system=float(metrics["cpu_system"]),
            max_resident_set_kb=int(metrics["max_resident_set_kb"]),
        )


class RunnerRegistry:
    def __init__(self, runners: Iterable[Runner] | None = None) -> None:
        self._runners: dict[str, Runner] = {}
        if runners:
            for runner in runners:
                self.register(runner)
        else:
            self.register(TimeRunner())
            self.register(DeploymentRunner())

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


def _parse_docker_maxrss(stderr: str) -> int | None:
    match = _DOCKER_MAXRSS_RE.search(stderr)
    if match is None:
        return None
    return int(match.group("value"))
