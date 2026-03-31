"""Benchmark discovery and execution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .definitions import BenchmarkDefinition, BenchmarkError
from . import fixtures as fixture_api
from .hashing import hash_benchmark, hash_file
from .hardware import environment_snapshot, hardware_key
from .paths import BenchPaths
from .runners import RunnerRegistry
from .specs import discover_definitions

_LOG = logging.getLogger(__name__)


@dataclass
class BenchmarkContext:
    definition: BenchmarkDefinition
    dataset_path: Path
    benchmark_hash: str
    input_hash: str
    root: Path


class BenchmarkExecutor:
    def __init__(
        self,
        paths: BenchPaths,
        tenzir_bin: Path,
        runner_registry: RunnerRegistry,
        tenzir_args: Sequence[str] = (),
        target: str | None = None,
        *,
        validate: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.paths = paths
        self.tenzir_bin = tenzir_bin
        self.tenzir_args = tuple(tenzir_args)
        self.target = target or _infer_target(tenzir_bin)
        self.runners = runner_registry
        self.validate_only = validate
        self.dry_run = dry_run
        self.verbose = verbose
        self._build_info: BuildInfo | None = None
        self._progress_total: int = 0
        self._progress_current: int = 0
        self._progress_planned: bool = False
        self._printed_commands: set[tuple[str, tuple[str, ...]]] = set()
        self._validated_invocation = False

    def discover(self, pattern: str | None, *, root: Path | None = None) -> Iterable[BenchmarkContext]:
        try:
            definitions = discover_definitions(
                pattern,
                version_supplier=lambda: self._get_build_info().version,
                root=root,
            )
        except BenchmarkError as exc:
            _LOG.error("%s", exc)
            return
        for definition in definitions:
            context = self.create_context(definition)
            if context:
                yield context

    def create_context(self, definition: BenchmarkDefinition) -> BenchmarkContext | None:
        dataset = self._ensure_dataset(definition)
        if not dataset.exists():
            _LOG.error("Dataset missing for %s: %s", definition.id, dataset)
            return None
        return BenchmarkContext(
            definition=definition,
            dataset_path=dataset,
            benchmark_hash=hash_benchmark(definition),
            input_hash=hash_file(dataset),
            root=_benchmark_repo_root(definition.path),
        )

    def _ensure_dataset(self, definition: BenchmarkDefinition) -> Path:
        input_path = Path(definition.input_path).expanduser()
        if input_path.is_absolute():
            return input_path.resolve()
        dataset = (self.paths.datasets_cache_dir / input_path).resolve()
        if dataset.exists() or definition.input_source is None:
            return dataset
        dataset.parent.mkdir(parents=True, exist_ok=True)
        source_value = definition.input_source.strip()
        parsed = urllib.parse.urlparse(source_value)
        if parsed.scheme in {"http", "https"}:
            request = urllib.request.Request(
                source_value,
                headers={"User-Agent": "tenzir-bench/0.1"},
            )
            with urllib.request.urlopen(request) as response, dataset.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            return dataset
        source = Path(source_value).expanduser()
        if not source.is_absolute():
            source = (definition.path.parent / source).resolve()
        else:
            source = source.resolve()
        if not source.exists():
            return dataset
        shutil.copy2(source, dataset)
        return dataset

    def prepare_progress(self, contexts: Sequence[BenchmarkContext]) -> None:
        total = sum(
            context.definition.runtime.warmup_runs
            + context.definition.runtime.measurement_runs
            for context in contexts
        )
        self._progress_total = total
        self._progress_current = 0
        self._progress_planned = total > 0

    def build_info(self) -> BuildInfo:
        return self._get_build_info()

    def ensure_reports(
        self,
        contexts: Iterable[BenchmarkContext],
        output_dir: Path,
        *,
        force: bool,
    ) -> Path:
        contexts = list(contexts)
        build = self.build_info()
        if not force:
            collected = self._collect_cached_reports(contexts, build)
            if collected:
                _LOG.info("Reusing cached reports from state cache for %s", self.tenzir_bin)
                return self._stage_reports(output_dir, build, collected)
        reports_generated: list[tuple[BenchmarkContext, Path]] = []
        if contexts:
            self.prepare_progress(contexts)
        for context in contexts:
            generated = self.execute(context)
            reports_generated.extend((context, report) for report in generated)
        return self._stage_reports(output_dir, build, reports_generated)

    def execute(self, context: BenchmarkContext) -> list[Path]:
        if self.dry_run:
            self._print_dry_run_invocation(context)
            _LOG.info("Dry run: resolved benchmark %s", context.definition.id)
            return []
        runner = self.runners.get(context.definition.runner)
        build = self._get_build_info()
        revision = _git_revision()
        output_root = self._result_dir(context, build)
        output_root.mkdir(parents=True, exist_ok=True)
        self._validate_invocation()
        command = _tenzir_command(
            self.tenzir_bin,
            [*self.tenzir_args, *context.definition.tenzir_args],
            pipeline_path=context.definition.path,
        )
        with _benchmark_runtime_env(context, output_root) as env:
            self._print_invocation_once(context, env, command)
            if self.validate_only:
                _LOG.info("Validate: validated benchmark %s", context.definition.id)
                return []
            results: list[Path] = []
            total_runs = context.definition.runtime.warmup_runs + context.definition.runtime.measurement_runs
            dynamic_progress = False
            if not self._progress_planned:
                self._progress_total = total_runs
                self._progress_current = 0
                dynamic_progress = True
            for warmup in range(context.definition.runtime.warmup_runs):
                prefix = self._progress_prefix()
                _LOG.info(
                    "%s Warmup %s/%s for %s",
                    prefix,
                    warmup + 1,
                    context.definition.runtime.warmup_runs,
                    context.definition.id,
                )
                _run_once(
                    runner=runner,
                    definition=context.definition,
                    env=env,
                    command=command,
                    binary_args=self.tenzir_args,
                    target=self.target,
                    timeout=context.definition.runtime.timeout_seconds,
                    output_root=output_root,
                    input_path=context.dataset_path,
                    benchmark_hash=context.benchmark_hash,
                    input_hash=context.input_hash,
                    build=build,
                    revision=revision,
                    store_result=False,
                    run_index=warmup,
                )
            for run_index in range(context.definition.runtime.measurement_runs):
                prefix = self._progress_prefix()
                _LOG.info(
                    "%s Measurement %s/%s for %s",
                    prefix,
                    run_index + 1,
                    context.definition.runtime.measurement_runs,
                    context.definition.id,
                )
                result = _run_once(
                    runner=runner,
                    definition=context.definition,
                    env=env,
                    command=command,
                    binary_args=self.tenzir_args,
                    target=self.target,
                    timeout=context.definition.runtime.timeout_seconds,
                    output_root=output_root,
                    input_path=context.dataset_path,
                    benchmark_hash=context.benchmark_hash,
                    input_hash=context.input_hash,
                    build=build,
                    revision=revision,
                    store_result=True,
                    run_index=run_index,
                )
                if result:
                    results.append(result)
            if self._progress_planned and self._progress_current >= self._progress_total:
                self._progress_planned = False
                self._progress_total = 0
                self._progress_current = 0
            elif dynamic_progress:
                self._progress_total = 0
                self._progress_current = 0
            return results

    def validate(self, context: BenchmarkContext) -> None:
        if self.dry_run:
            self._print_dry_run_invocation(context)
            return
        build = self._get_build_info()
        output_root = self._result_dir(context, build)
        self._validate_invocation()
        command = _tenzir_command(
            self.tenzir_bin,
            [*self.tenzir_args, *context.definition.tenzir_args],
            pipeline_path=context.definition.path,
        )
        with _benchmark_runtime_env(context, output_root) as env:
            self._print_invocation_once(context, env, command)

    def _print_invocation_once(
        self,
        context: BenchmarkContext,
        env: dict[str, str],
        command: Sequence[str],
    ) -> None:
        if not self.verbose:
            return
        key = (context.definition.id, tuple(command))
        if key in self._printed_commands:
            return
        self._printed_commands.add(key)
        env_items = [f"{name}={env[name]}" for name in sorted(env)]
        options = f" {' '.join(self.tenzir_args)}" if self.tenzir_args else ""
        print(f"# {context.definition.id} ({self.tenzir_bin}{options})")
        print(shlex.join(["env", *env_items, *command]))

    def _validate_invocation(self) -> None:
        if self._validated_invocation:
            return
        try:
            subprocess.run(
                _tenzir_command(
                    self.tenzir_bin,
                    self.tenzir_args,
                    pipeline="version | select version | write_ndjson",
                ),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
            raise RuntimeError(f"Invalid Tenzir invocation for {self.tenzir_bin}: {message}") from exc
        self._validated_invocation = True

    def _print_dry_run_invocation(self, context: BenchmarkContext) -> None:
        if context.definition.fixtures:
            _LOG.info(
                "Dry run skips fixture activation for %s",
                context.definition.id,
            )
        env = _benchmark_env(
            context.definition,
            context.dataset_path,
            self._dry_run_result_dir(context),
        )
        command = _tenzir_command(
            self.tenzir_bin,
            [*self.tenzir_args, *context.definition.tenzir_args],
            pipeline_path=context.definition.path,
        )
        self._print_invocation_once(context, env, command)

    def _progress_prefix(self) -> str:
        if self._progress_total <= 0:
            return ""
        self._progress_current += 1
        width = len(str(self._progress_total))
        return f"[{self._progress_current:>{width}}/{self._progress_total}]"

    def _result_dir(self, context: BenchmarkContext, build: BuildInfo) -> Path:
        return (
            self.paths.results_state_dir
            / context.benchmark_hash
            / context.input_hash
            / build_result_id(build, self.tenzir_args)
            / context.definition.runner
        )

    def _collect_cached_reports(
        self,
        contexts: Sequence[BenchmarkContext],
        build: BuildInfo,
    ) -> list[tuple[BenchmarkContext, Path]]:
        collected: list[tuple[BenchmarkContext, Path]] = []
        for context in contexts:
            run_dir = self._result_dir(context, build)
            collected.extend((context, report) for report in run_dir.glob("*.json"))
        return collected

    def _stage_reports(
        self,
        output_dir: Path,
        build: BuildInfo,
        reports: Sequence[tuple[BenchmarkContext, Path]],
    ) -> Path:
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        for context, report in reports:
            target = _staged_report_path(self.paths, output_dir, context, build, report)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(report, target)
        return output_dir

    def _dry_run_result_dir(self, context: BenchmarkContext) -> Path:
        return (
            self.paths.results_state_dir
            / context.benchmark_hash
            / context.input_hash
            / "dry-run"
            / context.definition.runner
        )

    def _get_build_info(self) -> BuildInfo:
        if self._build_info is None:
            self._build_info = _detect_build(self.tenzir_bin, self.tenzir_args)
        return self._build_info


# ---------------------------------------------------------------------------
# Helpers

@dataclass
class BuildInfo:
    version: str | None
    build_type: str | None
    path: str

    @property
    def build_id(self) -> str:
        return self.version or "unknown"


def _discover_files(pattern: str | None) -> list[Path]:
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


def _detect_build(tenzir_bin: Path, tenzir_args: Sequence[str]) -> BuildInfo:
    try:
        proc = subprocess.run(
            _tenzir_command(
                tenzir_bin,
                tenzir_args,
                pipeline='version | select version, build_type=build.type | write_ndjson',
            ),
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _LOG.warning("Failed to detect build metadata: %s", exc)
        return BuildInfo(version=None, build_type=None, path=str(tenzir_bin))
    line = proc.stdout.strip().splitlines()[0] if proc.stdout else ""
    data: dict[str, str] = json.loads(line)
    version = data["version"] if "version" in data else None
    build_type = data["build_type"] if "build_type" in data else None
    return BuildInfo(version=version, build_type=build_type, path=str(tenzir_bin.resolve()))


def _git_revision() -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        return proc.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _benchmark_repo_root(path: Path) -> Path:
    resolved = path.resolve()
    for ancestor in resolved.parents:
        bench_dir = ancestor / "bench"
        if not bench_dir.is_dir():
            continue
        try:
            resolved.relative_to(bench_dir)
        except ValueError:
            continue
        return ancestor
    return resolved.parent


@contextmanager
def _benchmark_runtime_env(
    context: BenchmarkContext,
    output_root: Path,
) -> Iterable[dict[str, str]]:
    fixture_api.load_fixture_modules(context.definition.path, root=context.root)
    env = _benchmark_env(context.definition, context.dataset_path, output_root)
    token = fixture_api.push_context(
        fixture_api.FixtureContext(
            definition=context.definition,
            dataset_path=context.dataset_path,
            output_root=output_root,
            env=dict(env),
        ),
    )
    try:
        with fixture_api.activate(context.definition.fixtures) as fixture_env:
            merged = dict(env)
            merged.update(fixture_env)
            _refresh_forwarded_env(merged)
            yield merged
    finally:
        fixture_api.pop_context(token)


def _run_once(
    runner,
    definition: BenchmarkDefinition,
    env: dict[str, str],
    command: Sequence[str],
    binary_args: Sequence[str],
    target: str,
    timeout: int | None,
    output_root: Path,
    input_path: Path,
    benchmark_hash: str,
    input_hash: str,
    build: BuildInfo,
    revision: str | None,
    store_result: bool,
    run_index: int,
) -> Path | None:
    output_file = None
    if definition.output_path:
        output_file = output_root / "outputs" / definition.output_path
        if output_file.exists():
            output_file.unlink()
    run_env = {**os.environ, **env}
    phase = "measurement" if store_result else "warmup"
    fixture_api.invoke_active_hook(
        "before_run",
        definition=definition,
        phase=phase,
        run_index=run_index,
        env=run_env,
        command=tuple(command),
        input_path=input_path,
        output_path=output_file,
    )
    metrics = None
    try:
        metrics = runner.run(command, env=run_env, timeout=timeout)
    except RuntimeError as exc:
        _LOG.error("Runner failed for %s: %s", definition.id, exc)
        return None
    finally:
        fixture_api.invoke_active_hook(
            "after_run",
            definition=definition,
            phase=phase,
            run_index=run_index,
            env=run_env,
            command=tuple(command),
            input_path=input_path,
            output_path=output_file,
            success=metrics is not None,
        )
    if not store_result:
        return None
    output_bytes = output_file.stat().st_size if output_file and output_file.exists() else 0
    input_bytes = input_path.stat().st_size if definition.input_measure else output_bytes
    if input_bytes is None:
        input_bytes = 0
    timestamp = datetime.now(UTC)
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
    snapshot = environment_snapshot()
    report = {
        "pipeline": definition.id,
        "benchmark_id": definition.benchmark_id or definition.id,
        "implementation_id": definition.implementation_id,
        "target": target,
        "revision": revision,
        "build": {
            "version": build.version,
            "type": build.build_type,
            "path": build.path,
            "options": list(binary_args),
        },
        "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": snapshot,
        "hardware": {
            **snapshot["hardware"],
            "key": hardware_key(snapshot),
        },
        "definition": {
            "benchmark_hash": benchmark_hash,
            "input_hash": input_hash,
        },
        "command": command,
        "tags": definition.tags,
        "fixtures": [
            {"name": fixture.name, "options": fixture.options}
            for fixture in definition.fixtures
        ],
        "input": {
            "path": str(input_path),
            "bytes": input_path.stat().st_size,
            "records": definition.input_events,
            "measure": "input" if definition.input_measure else "output",
        },
        "runner": definition.runner,
        "runtime": runtime,
        "throughput": throughput,
        "measurement": {
            "run_index": run_index,
            "timestamp": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
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


def build_result_id(build: BuildInfo, tenzir_args: Sequence[str]) -> str:
    if not tenzir_args:
        return build.build_id
    digest = hashlib.sha256("\0".join(tenzir_args).encode("utf-8")).hexdigest()[:12]
    return f"{build.build_id}-{digest}"


def _staged_report_path(
    paths: BenchPaths,
    output_dir: Path,
    context: BenchmarkContext,
    build: BuildInfo,
    report: Path,
) -> Path:
    try:
        relative = report.relative_to(paths.results_state_dir)
    except ValueError:
        relative = Path(
            context.benchmark_hash,
            context.input_hash,
            build.build_id,
            context.definition.runner,
            report.name,
        )
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _tenzir_command(
    tenzir_bin: Path,
    tenzir_args: Sequence[str],
    *,
    pipeline: str | None = None,
    pipeline_path: Path | None = None,
) -> list[str]:
    command = [str(tenzir_bin), *tenzir_args]
    if pipeline is not None:
        command.append(pipeline)
    if pipeline_path is not None:
        command.extend(["--file", str(pipeline_path)])
    return command


def _benchmark_env(
    definition: BenchmarkDefinition,
    dataset: Path,
    output_root: Path,
) -> dict[str, str]:
    env = {"BENCHMARK_INPUT_PATH": str(dataset)}
    env.update(definition.env)
    if definition.output_path:
        output_file = output_root / "outputs" / definition.output_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        env["BENCHMARK_OUTPUT_PATH"] = str(output_file)
    _refresh_forwarded_env(env)
    return env


def _infer_target(tenzir_bin: Path) -> str:
    if tenzir_bin.suffix == ".sh" and tenzir_bin.parent.name == "docker":
        return "docker"
    return "static"


def _refresh_forwarded_env(env: dict[str, str]) -> None:
    forward_keys = sorted(key for key in env if key != "TENZIR_BENCH_FORWARD_ENV")
    env["TENZIR_BENCH_FORWARD_ENV"] = ",".join(forward_keys)
