"""Parsing and validation of benchmark definitions."""

from __future__ import annotations

import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

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
    frontmatter, body = split_frontmatter(raw_text, path)
    data = _load_yaml_mapping(frontmatter, path, "frontmatter")
    benchmark = _require_mapping(data.get("benchmark"), path, "benchmark")
    benchmark_id = _require_str(benchmark, "id", path)
    description = benchmark.get("description")
    if description is not None and not isinstance(description, str):
        raise BenchmarkError(f"{path}: benchmark.description must be a string")
    tags = _require_str_mapping(benchmark.get("tags") or {}, path, "benchmark.tags")
    min_version = benchmark.get("min_version")
    if min_version is not None and not isinstance(min_version, str):
        raise BenchmarkError(f"{path}: benchmark.min_version must be a string")
    max_version = benchmark.get("max_version")
    if max_version is not None and not isinstance(max_version, str):
        raise BenchmarkError(f"{path}: benchmark.max_version must be a string")
    input_section = _require_mapping(benchmark.get("input"), path, "benchmark.input")
    input_path = _require_str(input_section, "path", path)
    input_source = _optional_str(input_section, "source", path, prefix="benchmark.input")
    input_events = input_section.get("events")
    if input_events is not None and not isinstance(input_events, int):
        raise BenchmarkError(f"{path}: benchmark.input.events must be an integer")
    input_measure = bool(input_section.get("measure", True))
    output_section = benchmark.get("output")
    output_path: str | None = None
    output_measure = False
    if output_section is not None:
        output_mapping = _require_mapping(output_section, path, "benchmark.output")
        output_path = _optional_str(output_mapping, "path", path, prefix="benchmark.output")
        output_measure = bool(output_mapping.get("measure", False))
    if input_measure and output_measure:
        raise BenchmarkError(f"{path}: only one of input.measure or output.measure can be true")
    if not input_measure and not output_measure:
        raise BenchmarkError(f"{path}: one of input.measure or output.measure must be true")
    if output_measure and output_path is None:
        raise BenchmarkError(
            f"{path}: benchmark.output.path is required when output.measure is true"
        )
    env = _require_str_mapping(benchmark.get("env") or {}, path, "benchmark.env")
    fixture_specs = parse_fixture_specs(benchmark, path)
    tenzir_args = _require_str_list(
        benchmark.get("tenzir_args") or [],
        path,
        "benchmark.tenzir_args",
    )
    runner = benchmark.get("runner", "time")
    if not isinstance(runner, str):
        raise BenchmarkError(f"{path}: benchmark.runner must be a string")
    runtime_section = _require_mapping(
        benchmark.get("runtime") or {},
        path,
        "benchmark.runtime",
    )
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


def _load_yaml_mapping(text: str, path: pathlib.Path, label: str) -> dict[str, object]:
    try:
        payload = cast(object, yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise BenchmarkError(f"Failed to parse YAML {label} in {path}: {exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise BenchmarkError(f"{path}: YAML {label} must be a mapping")
    return _string_key_mapping(cast(Mapping[object, object], payload), path, label)


def _string_key_mapping(
    value: Mapping[object, object],
    path: pathlib.Path,
    label: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            raise BenchmarkError(f"{path}: {label} keys must be strings")
        result[key] = entry
    return result


def _require_mapping(value: object, path: pathlib.Path, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{path}: {label} must be a mapping")
    return _string_key_mapping(cast(Mapping[object, object], value), path, label)


def _require_str_mapping(value: object, path: pathlib.Path, label: str) -> dict[str, str]:
    mapping = _require_mapping(value, path, label)
    result: dict[str, str] = {}
    for key, entry in mapping.items():
        if not isinstance(entry, str):
            raise BenchmarkError(f"{path}: {label} must be a mapping of strings")
        result[key] = entry
    return result


def _require_str_list(value: object, path: pathlib.Path, label: str) -> list[str]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{path}: {label} must be a list of strings")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            raise BenchmarkError(f"{path}: {label} must be a list of strings")
        result.append(item)
    return result


def split_frontmatter(text: str, path: pathlib.Path) -> tuple[str, str]:
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


def _optional_str(
    mapping: dict[str, object],
    key: str,
    path: pathlib.Path,
    *,
    prefix: str,
) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BenchmarkError(f"{path}: {prefix}.{key} must be a string")
    return value


def parse_fixture_specs(
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
        raw = list(cast(list[object], value))
    elif isinstance(value, str):
        raw = [value]
    elif isinstance(value, Mapping):
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
        if not isinstance(entry, Mapping):
            raise BenchmarkError(
                f"{path}: fixture entries must be strings or mappings, got {type(entry).__name__}",
            )
        entry_mapping = _string_key_mapping(cast(Mapping[object, object], entry), path, "fixture")
        if len(entry_mapping) != 1:
            raise BenchmarkError(
                f"{path}: fixture mappings must contain exactly one key, got {list(entry_mapping.keys())}",
            )
        name, options = next(iter(entry_mapping.items()))
        if not name.strip():
            raise BenchmarkError(f"{path}: fixture names must be non-empty strings")
        specs.append(
            FixtureSpec(
                name=name.strip(),
                options=_require_mapping(options, path, f"fixture options for '{name}'"),
            )
        )
    return tuple(specs)
