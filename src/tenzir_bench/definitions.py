"""Parsing and validation of benchmark definitions."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import yaml

from .fixtures import FixtureSpec


class BenchmarkError(Exception):
    """Raised when a benchmark definition is invalid."""


@dataclass(frozen=True)
class BenchmarkRuntime:
    warmup_runs: int = 0
    measurement_runs: int = 1
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class BenchmarkDefinition:
    path: pathlib.Path
    id: str
    description: str | None
    tags: dict[str, str]
    min_version: str | None
    max_version: str | None
    input_path: str
    input_source: str | None
    input_events: int | None
    input_measure: bool
    output_path: str | None
    output_measure: bool
    env: dict[str, str]
    fixtures: tuple[FixtureSpec, ...]
    tenzir_args: list[str]
    runner: str
    runtime: BenchmarkRuntime
    pipeline_body: str
    benchmark_id: str | None = None
    implementation_id: str | None = None


def parse_benchmark_file(path: pathlib.Path) -> BenchmarkDefinition:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw_text, path)
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise BenchmarkError(f"Failed to parse YAML frontmatter in {path}: {exc}") from exc
    benchmark = data.get("benchmark")
    if not isinstance(benchmark, dict):
        raise BenchmarkError(f"{path}: missing 'benchmark' mapping in frontmatter")
    benchmark_id = _require_str(benchmark, "id", path)
    description = benchmark.get("description")
    if description is not None and not isinstance(description, str):
        raise BenchmarkError(f"{path}: benchmark.description must be a string")
    tags = benchmark.get("tags") or {}
    if not isinstance(tags, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in tags.items()
    ):
        raise BenchmarkError(f"{path}: benchmark.tags must be a mapping of strings")
    min_version = benchmark.get("min_version")
    if min_version is not None and not isinstance(min_version, str):
        raise BenchmarkError(f"{path}: benchmark.min_version must be a string")
    max_version = benchmark.get("max_version")
    if max_version is not None and not isinstance(max_version, str):
        raise BenchmarkError(f"{path}: benchmark.max_version must be a string")
    input_section = benchmark.get("input")
    if not isinstance(input_section, dict):
        raise BenchmarkError(f"{path}: benchmark.input must be a mapping")
    input_path = _require_str(input_section, "path", path)
    input_source = input_section.get("source")
    if input_source is not None and not isinstance(input_source, str):
        raise BenchmarkError(f"{path}: benchmark.input.source must be a string")
    input_events = input_section.get("events")
    if input_events is not None and not isinstance(input_events, int):
        raise BenchmarkError(f"{path}: benchmark.input.events must be an integer")
    input_measure = bool(input_section.get("measure", True))
    output_section = benchmark.get("output")
    output_path: str | None = None
    output_measure = False
    if output_section is not None:
        if not isinstance(output_section, dict):
            raise BenchmarkError(f"{path}: benchmark.output must be a mapping")
        output_path = output_section.get("path")
        if output_path is not None and not isinstance(output_path, str):
            raise BenchmarkError(f"{path}: benchmark.output.path must be a string")
        output_measure = bool(output_section.get("measure", False))
    if input_measure and output_measure:
        raise BenchmarkError(f"{path}: only one of input.measure or output.measure can be true")
    if not input_measure and not output_measure:
        raise BenchmarkError(f"{path}: one of input.measure or output.measure must be true")
    if output_measure and output_path is None:
        raise BenchmarkError(
            f"{path}: benchmark.output.path is required when output.measure is true"
        )
    env = benchmark.get("env") or {}
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        raise BenchmarkError(f"{path}: benchmark.env must be a mapping of strings")
    fixture_specs = _parse_fixture_specs(benchmark, path)
    tenzir_args = benchmark.get("tenzir_args") or []
    if not isinstance(tenzir_args, list) or not all(isinstance(item, str) for item in tenzir_args):
        raise BenchmarkError(f"{path}: benchmark.tenzir_args must be a list of strings")
    runner = benchmark.get("runner", "time")
    if not isinstance(runner, str):
        raise BenchmarkError(f"{path}: benchmark.runner must be a string")
    runtime_section = benchmark.get("runtime") or {}
    if not isinstance(runtime_section, dict):
        raise BenchmarkError(f"{path}: benchmark.runtime must be a mapping")
    warmup_runs = runtime_section.get("warmup_runs", 0)
    measurement_runs = runtime_section.get("measurement_runs", 1)
    timeout_seconds = runtime_section.get("timeout_seconds")
    if not isinstance(warmup_runs, int) or warmup_runs < 0:
        raise BenchmarkError(
            f"{path}: benchmark.runtime.warmup_runs must be a non-negative integer"
        )
    if not isinstance(measurement_runs, int) or measurement_runs <= 0:
        raise BenchmarkError(
            f"{path}: benchmark.runtime.measurement_runs must be a positive integer"
        )
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, int) or timeout_seconds <= 0
    ):
        raise BenchmarkError(
            f"{path}: benchmark.runtime.timeout_seconds must be a positive integer"
        )
    runtime = BenchmarkRuntime(
        warmup_runs=warmup_runs,
        measurement_runs=measurement_runs,
        timeout_seconds=timeout_seconds,
    )
    return BenchmarkDefinition(
        path=path,
        id=benchmark_id,
        description=description,
        tags=dict(tags),
        min_version=min_version,
        max_version=max_version,
        input_path=input_path,
        input_source=input_source,
        input_events=input_events,
        input_measure=input_measure,
        output_path=output_path,
        output_measure=output_measure,
        env=dict(env),
        fixtures=fixture_specs,
        tenzir_args=list(tenzir_args),
        runner=runner,
        runtime=runtime,
        pipeline_body=body,
        benchmark_id=benchmark_id,
    )


def _split_frontmatter(text: str, path: pathlib.Path) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise BenchmarkError(
            f"{path}: benchmark file must start with YAML frontmatter delimited by '---'",
        )
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            front = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            return front, body.strip()
    raise BenchmarkError(f"{path}: unable to find closing '---' for frontmatter")


def _require_str(mapping: dict[str, object], key: str, path: pathlib.Path) -> str:
    value = mapping.get(key)
    if value is None:
        raise BenchmarkError(f"{path}: missing required key benchmark.{key}")
    if not isinstance(value, str):
        raise BenchmarkError(f"{path}: benchmark.{key} must be a string")
    return value


def _parse_fixture_specs(
    benchmark: dict[str, object],
    path: pathlib.Path,
) -> tuple[FixtureSpec, ...]:
    if "fixture" in benchmark and "fixtures" in benchmark:
        raise BenchmarkError(
            f"{path}: benchmark.fixture and benchmark.fixtures are mutually exclusive"
        )
    if "fixture" in benchmark:
        return _normalize_fixture_specs(benchmark["fixture"], path)
    if "fixtures" in benchmark:
        return _normalize_fixture_specs(benchmark["fixtures"], path)
    return ()


def _normalize_fixture_specs(
    value: object,
    path: pathlib.Path,
) -> tuple[FixtureSpec, ...]:
    raw: list[object]
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, dict):
        raw = [value]
    else:
        raise BenchmarkError(
            f"{path}: benchmark.fixtures must be a string, mapping, or list",
        )

    specs: list[FixtureSpec] = []
    for entry in raw:
        if isinstance(entry, str):
            name = entry.strip()
            if not name:
                raise BenchmarkError(f"{path}: fixture names must be non-empty strings")
            specs.append(FixtureSpec(name=name))
            continue
        if not isinstance(entry, dict):
            raise BenchmarkError(
                f"{path}: fixture entries must be strings or mappings, got {type(entry).__name__}",
            )
        if len(entry) != 1:
            raise BenchmarkError(
                f"{path}: fixture mappings must contain exactly one key, got {list(entry.keys())}",
            )
        name, options = next(iter(entry.items()))
        if not isinstance(name, str) or not name.strip():
            raise BenchmarkError(f"{path}: fixture names must be non-empty strings")
        if not isinstance(options, dict):
            raise BenchmarkError(
                f"{path}: fixture options for '{name}' must be a mapping",
            )
        specs.append(FixtureSpec(name=name.strip(), options=options))
    return tuple(specs)
