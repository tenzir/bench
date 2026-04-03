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
class BenchmarkInput:
    name: str
    path: str
    source_url: str | None
    source_num_events: int | None
    repetitions: int = 1

    @property
    def total_events(self) -> int | None:
        if self.source_num_events is None:
            return None
        return self.source_num_events * self.repetitions


@dataclass(frozen=True)
class BenchmarkDefinition:
    path: pathlib.Path
    id: str
    description: str | None
    tags: dict[str, str]
    min_version: str | None
    max_version: str | None
    inputs: dict[str, BenchmarkInput]
    output_path: str | None
    env: dict[str, str]
    fixtures: tuple[FixtureSpec, ...]
    tenzir_args: list[str]
    runner: str
    runtime: BenchmarkRuntime
    pipeline_body: str
    benchmark_id: str | None = None
    implementation_id: str | None = None

    @property
    def input_names(self) -> tuple[str, ...]:
        return tuple(self.inputs.keys())

    @property
    def input_events(self) -> int | None:
        totals = [input_definition.total_events for input_definition in self.inputs.values()]
        if any(total is None for total in totals):
            return None
        return sum(cast(list[int], totals))

    @property
    def input_path(self) -> str:
        return self.default_input.path

    @property
    def input_source(self) -> str | None:
        return self.default_input.source_url

    @property
    def input_source_url(self) -> str | None:
        return self.default_input.source_url

    @property
    def input_source_num_events(self) -> int | None:
        return self.default_input.source_num_events

    @property
    def input_repetitions(self) -> int:
        return self.default_input.repetitions

    @property
    def default_input(self) -> BenchmarkInput:
        if len(self.inputs) != 1:
            raise BenchmarkError(
                f"{self.path}: benchmark exposes multiple inputs; use named inputs instead"
            )
        return next(iter(self.inputs.values()))


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
    inputs = parse_inputs(benchmark, path, prefix="benchmark")
    output_section = benchmark.get("output")
    output_path: str | None = None
    if output_section is not None:
        output_mapping = _require_mapping(output_section, path, "benchmark.output")
        output_path = _optional_str(output_mapping, "path", path, prefix="benchmark.output")
    if output_section is not None and output_path is None:
        raise BenchmarkError(f"{path}: benchmark.output.path must be a string")
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
        inputs=inputs,
        output_path=output_path,
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


def parse_input_source(
    input_section: dict[str, object],
    path: pathlib.Path,
    *,
    prefix: str,
) -> tuple[str | None, int | None]:
    source = input_section.get("source")
    legacy_events = input_section.get("events")
    if legacy_events is not None and not isinstance(legacy_events, int):
        raise BenchmarkError(f"{path}: {prefix}.events must be an integer")
    if source is None:
        return None, legacy_events
    if isinstance(source, str):
        return source, legacy_events
    source_mapping = _require_mapping(source, path, f"{prefix}.source")
    if legacy_events is not None:
        raise BenchmarkError(
            f"{path}: {prefix}.events cannot be combined with {prefix}.source.num_events"
        )
    source_url = _optional_str(source_mapping, "url", path, prefix=f"{prefix}.source")
    num_events = source_mapping.get("num_events")
    if num_events is not None and not isinstance(num_events, int):
        raise BenchmarkError(f"{path}: {prefix}.source.num_events must be an integer")
    return source_url, num_events


def parse_inputs(
    benchmark: dict[str, object],
    path: pathlib.Path,
    *,
    prefix: str,
) -> dict[str, BenchmarkInput]:
    if "input" in benchmark:
        raise BenchmarkError(
            f"{path}: {prefix}.input is no longer supported; use {prefix}.inputs.<name>"
        )
    raw_inputs = _require_mapping(benchmark.get("inputs"), path, f"{prefix}.inputs")
    if not raw_inputs:
        raise BenchmarkError(f"{path}: {prefix}.inputs must not be empty")
    inputs: dict[str, BenchmarkInput] = {}
    for input_name, input_value in raw_inputs.items():
        if not input_name.strip():
            raise BenchmarkError(f"{path}: {prefix}.inputs keys must be non-empty strings")
        input_mapping = _require_mapping(input_value, path, f"{prefix}.inputs.{input_name}")
        input_path = _require_str(input_mapping, "path", path)
        source_url, source_num_events = parse_input_source(
            input_mapping,
            path,
            prefix=f"{prefix}.inputs.{input_name}",
        )
        repetitions = input_mapping.get("repetitions", 1)
        if not isinstance(repetitions, int) or repetitions <= 0:
            raise BenchmarkError(
                f"{path}: {prefix}.inputs.{input_name}.repetitions must be a positive integer"
            )
        inputs[input_name] = BenchmarkInput(
            name=input_name,
            path=input_path,
            source_url=source_url,
            source_num_events=source_num_events,
            repetitions=repetitions,
        )
    return inputs


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
        normalized_options = _require_mapping(options, path, f"fixture options for '{name}'")
        selected_inputs = normalized_options.pop("inputs", ())
        fixture_inputs = _require_str_tuple(
            selected_inputs,
            path,
            f"fixture options for '{name}'.inputs",
        )
        specs.append(
            FixtureSpec(
                name=name.strip(),
                options=normalized_options,
                inputs=fixture_inputs,
            )
        )
    return tuple(specs)


def _require_str_tuple(value: object, path: pathlib.Path, label: str) -> tuple[str, ...]:
    if value in ((), None):
        return ()
    if not isinstance(value, list):
        raise BenchmarkError(f"{path}: {label} must be a list of strings")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise BenchmarkError(f"{path}: {label} must be a list of non-empty strings")
        result.append(item)
    return tuple(result)
