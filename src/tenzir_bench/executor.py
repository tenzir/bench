"""Benchmark discovery and execution."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from .definitions import BenchmarkDefinition, BenchmarkError, parse_benchmark_file
from .hashing import hash_benchmark, hash_file
from .paths import BenchPaths
from .runners import RunnerRegistry

_LOG = logging.getLogger(__name__)


@dataclass
class BenchmarkContext:
    definition: BenchmarkDefinition
    dataset_path: Path
    benchmark_hash: str
    input_hash: str


class BenchmarkExecutor:
    def __init__(self, paths: BenchPaths, tenzir_bin: Path, runner_registry: RunnerRegistry) -> None:
        self.paths = paths
        self.tenzir_bin = tenzir_bin
        self.runners = runner_registry

    def discover(self, pattern: Optional[str]) -> Iterable[BenchmarkContext]:
        files = _discover_files(pattern)
        for file in files:
            try:
                definition = parse_benchmark_file(file)
            except BenchmarkError as exc:
                _LOG.error("Skipping %s: %s", file, exc)
                continue
            dataset = (self.paths.datasets_cache_dir / definition.input_path).resolve()
            if not dataset.exists():
                _LOG.error("Dataset missing for %s: %s", definition.id, dataset)
                continue
            context = BenchmarkContext(
                definition=definition,
                dataset_path=dataset,
                benchmark_hash=hash_benchmark(definition),
                input_hash=hash_file(dataset),
            )
            yield context

    def execute(self, context: BenchmarkContext) -> None:
        runner = self.runners.get(context.definition.runner)
        build = _detect_build(self.tenzir_bin)
        revision = _git_revision()
        output_root = (
            self.paths.results_state_dir
            / context.benchmark_hash
            / context.input_hash
            / build.build_id
            / context.definition.runner
        )
        output_root.mkdir(parents=True, exist_ok=True)
        results: list[Path] = []
        for warmup in range(context.definition.runtime.warmup_runs):
            _LOG.info("Warmup %s/%s for %s", warmup + 1, context.definition.runtime.warmup_runs, context.definition.id)
            _run_once(
                runner=runner,
                definition=context.definition,
                dataset=context.dataset_path,
                tenzir_bin=self.tenzir_bin,
                timeout=context.definition.runtime.timeout_seconds,
                output_root=output_root,
                build=build,
                revision=revision,
                store_result=False,
                run_index=-1,
            )
        for run_index in range(context.definition.runtime.measurement_runs):
            _LOG.info(
                "Measurement %s/%s for %s",
                run_index + 1,
                context.definition.runtime.measurement_runs,
                context.definition.id,
            )
            result = _run_once(
                runner=runner,
                definition=context.definition,
                dataset=context.dataset_path,
                tenzir_bin=self.tenzir_bin,
                timeout=context.definition.runtime.timeout_seconds,
                output_root=output_root,
                build=build,
                revision=revision,
                store_result=True,
                run_index=run_index,
            )
            if result:
                results.append(result)
        return results


# ---------------------------------------------------------------------------
# Helpers

@dataclass
class BuildInfo:
    version: Optional[str]
    build_type: Optional[str]
    path: str

    @property
    def build_id(self) -> str:
        return self.version or "unknown"


def _discover_files(pattern: Optional[str]) -> List[Path]:
    root = Path("benchmarks")
    if pattern:
        candidate = Path(pattern)
        if candidate.exists():
            return [candidate.resolve()]
    if not root.exists():
        return []
    files = sorted(root.rglob("*.tql"))
    if pattern:
        from fnmatch import fnmatch

        files = [f for f in files if fnmatch(str(f), pattern)]
    return files


def _detect_build(tenzir_bin: Path) -> BuildInfo:
    try:
        proc = subprocess.run(
            [str(tenzir_bin), "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _LOG.warning("Failed to detect build metadata: %s", exc)
        return BuildInfo(version=None, build_type=None, path=str(tenzir_bin))
    line = proc.stdout.strip().splitlines()[0] if proc.stdout else ""
    version = None
    build_type = None
    if line:
        parts = line.split()
        if len(parts) >= 2:
            version = parts[1]
        if "(" in line and ")" in line:
            build_type = line.split("(", 1)[1].split(")", 1)[0]
    return BuildInfo(version=version, build_type=build_type, path=str(tenzir_bin.resolve()))


def _git_revision() -> Optional[str]:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        return proc.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _run_once(
    runner,
    definition: BenchmarkDefinition,
    dataset: Path,
    tenzir_bin: Path,
    timeout: Optional[int],
    output_root: Path,
    build: BuildInfo,
    revision: Optional[str],
    store_result: bool,
    run_index: int,
) -> Optional[Path]:
    env = {"BENCHMARK_INPUT_PATH": str(dataset)}
    for key, value in definition.env.items():
        env[key] = value
    output_file = None
    if definition.output_path:
        output_file = output_root / "outputs" / definition.output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if output_file.exists():
            output_file.unlink()
        env["BENCHMARK_OUTPUT_PATH"] = str(output_file)
    env = {**os.environ, **env}
    with tempfile.NamedTemporaryFile("w", suffix=".tql", delete=False) as tmp:
        tmp.write(definition.pipeline_body + "\n")
        pipeline_path = Path(tmp.name)
    try:
        command = [str(tenzir_bin), "--file", str(pipeline_path)] + definition.tenzir_args
        metrics = runner.run(command, env=env, timeout=timeout)
    finally:
        pipeline_path.unlink(missing_ok=True)
    if not store_result:
        return None
    output_bytes = output_file.stat().st_size if output_file and output_file.exists() else 0
    input_bytes = dataset.stat().st_size if definition.input_measure else output_bytes
    if input_bytes is None:
        input_bytes = 0
    timestamp = datetime.now(timezone.utc)
    runtime = {
        "wall_clock": metrics.wall_clock,
        "cpu_user": metrics.cpu_user,
        "cpu_system": metrics.cpu_system,
        "max_resident_set_kb": metrics.max_resident_set_kb,
        "exit_code": 0,
    }
    throughput = {
        "bytes_total_processed": input_bytes,
        "bytes_per_second": metrics.bytes_per_second(input_bytes),
    }
    if definition.input_events is not None and metrics.wall_clock:
        throughput["records_total_processed"] = definition.input_events
        throughput["records_per_second"] = definition.input_events / metrics.wall_clock
    report = {
        "pipeline": definition.id,
        "revision": revision,
        "build": {
            "version": build.version,
            "type": build.build_type,
            "path": build.path,
        },
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": _environment_snapshot(),
        "command": command,
        "tags": definition.tags,
        "input": {
            "path": str(dataset),
            "bytes": dataset.stat().st_size,
            "records": definition.input_events,
            "measure": "input" if definition.input_measure else "output",
        },
        "runner": definition.runner,
        "runtime": runtime,
        "throughput": throughput,
        "measurement": {
            "run_index": run_index,
        },
    }
    if definition.output_measure:
        report["output"] = {
            "path": str(output_file) if output_file else None,
            "bytes": output_bytes,
        }
    file_name = f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_run{run_index}.json"
    output_path = output_root / file_name
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    return output_path


def _environment_snapshot() -> dict:
    import platform

    return {
        "hostname": socket.gethostname(),
        "os": {
            "name": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
        },
        "hardware": {
            "cores": os.cpu_count(),
        },
    }
