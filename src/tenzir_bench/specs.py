"""Discovery and parsing of benchmark specs under ``bench/``."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import re

import yaml

from tenzir_bench.definitions import (
    BenchmarkDefinition,
    BenchmarkError,
    BenchmarkRuntime,
    _parse_fixture_specs,
    _split_frontmatter,
)

_VERSION_RE = re.compile(r"^v?(?P<body>[0-9xX*]+(?:\.[0-9xX*]+){0,2})")
_MAX_SENTINEL = 999_999


def discover_definitions(
    pattern: str | None,
    *,
    version_supplier: Callable[[], str | None],
    root: Path | None = None,
) -> list[BenchmarkDefinition]:
    resolved_root = (root or Path.cwd()).resolve()
    if pattern:
        candidate = Path(pattern)
        if candidate.exists():
            return load_definitions_from_paths(
                [candidate.resolve()],
                version_supplier=version_supplier,
                root=resolved_root,
            )
    bench_root = resolved_root / "bench"
    if bench_root.exists():
        return _load_bench_root(
            bench_root,
            patterns=[pattern] if pattern else None,
            version_supplier=version_supplier,
        )
    return _discover_legacy_definitions(resolved_root, pattern)


def load_definitions_from_paths(
    paths: Sequence[Path],
    *,
    version_supplier: Callable[[], str | None],
    root: Path | None = None,
) -> list[BenchmarkDefinition]:
    resolved_root = (root or Path.cwd()).resolve()
    seen: set[Path] = set()
    definitions: list[BenchmarkDefinition] = []
    pending_spec_paths: list[Path] = []
    for entry in paths:
        resolved = entry.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _is_spec_root(resolved):
            pending_spec_paths.append(resolved)
            continue
        if _is_spec_implementation_file(resolved):
            pending_spec_paths.append(resolved)
            continue
        if resolved.is_file() and resolved.suffix == ".tql":
            definitions.append(_load_legacy_definition(resolved))
            continue
        if resolved.is_dir():
            for file in sorted(resolved.rglob("*.tql")):
                real = file.resolve()
                if real in seen:
                    continue
                seen.add(real)
                if _is_spec_implementation_file(real):
                    pending_spec_paths.append(real)
                    continue
                definitions.append(_load_legacy_definition(real))
            continue
        raise BenchmarkError(f"{entry}: benchmark path does not exist")
    if pending_spec_paths:
        definitions.extend(
            _load_spec_entries(
                pending_spec_paths,
                version=version_supplier(),
                root=resolved_root,
            ),
        )
    return definitions


def load_default_patterns(bench_root: Path) -> list[str]:
    defaults_path = bench_root / "defaults.txt"
    if not defaults_path.exists():
        return []
    patterns: list[str] = []
    for raw_line in defaults_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def _load_bench_root(
    bench_root: Path,
    *,
    patterns: Sequence[str] | None,
    version_supplier: Callable[[], str | None],
) -> list[BenchmarkDefinition]:
    selected = _select_benchmark_dirs(bench_root, patterns)
    return _load_spec_entries(selected, version=version_supplier(), root=bench_root.parent)


def _discover_legacy_definitions(root: Path, pattern: str | None) -> list[BenchmarkDefinition]:
    legacy_root = root / "benchmarks"
    if pattern:
        candidate = Path(pattern)
        if candidate.exists():
            return [_load_legacy_definition(candidate.resolve())]
    if not legacy_root.exists():
        return []
    files = sorted(legacy_root.rglob("*.tql"))
    if pattern:
        from fnmatch import fnmatch

        files = [file for file in files if fnmatch(str(file), pattern)]
    return [_load_legacy_definition(file.resolve()) for file in files]


def _load_legacy_definition(path: Path) -> BenchmarkDefinition:
    from tenzir_bench.definitions import parse_benchmark_file

    return parse_benchmark_file(path)


def _select_benchmark_dirs(bench_root: Path, patterns: Sequence[str] | None) -> list[Path]:
    benchmark_dirs = sorted(
        directory
        for directory in bench_root.iterdir()
        if directory.is_dir() and (directory / "bench.yaml").exists()
    )
    if not patterns:
        patterns = load_default_patterns(bench_root)
    if not patterns:
        return benchmark_dirs
    from fnmatch import fnmatch

    selected: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for directory in benchmark_dirs:
            if fnmatch(directory.name, pattern) and directory not in seen:
                selected.append(directory)
                seen.add(directory)
    return selected


def _load_spec_entries(
    entries: Sequence[Path],
    *,
    version: str | None,
    root: Path,
) -> list[BenchmarkDefinition]:
    definitions: list[BenchmarkDefinition] = []
    loaded_roots: set[Path] = set()
    seen_impls: set[Path] = set()
    for entry in entries:
        resolved = entry.resolve()
        if _is_spec_implementation_file(resolved):
            implementation = _load_spec_implementation(resolved, version=version)
            if implementation is not None and resolved not in seen_impls:
                definitions.append(implementation)
                seen_impls.add(resolved)
            continue
        benchmark_root = resolved if resolved.is_dir() else resolved.parent
        if benchmark_root in loaded_roots:
            continue
        loaded_roots.add(benchmark_root)
        definitions.extend(_load_benchmark_dir(benchmark_root, version=version))
    definitions.sort(key=lambda definition: (definition.benchmark_id or definition.id, definition.implementation_id or definition.id))
    return definitions


def _load_benchmark_dir(directory: Path, *, version: str | None) -> list[BenchmarkDefinition]:
    metadata = _parse_benchmark_metadata(directory / "bench.yaml")
    definitions: list[BenchmarkDefinition] = []
    for file in sorted(directory.glob("*.tql")):
        definition = _parse_spec_implementation(file, directory.name, metadata)
        if definition is None:
            continue
        if _implementation_matches_version(definition, version):
            definitions.append(definition)
    return definitions


def _load_spec_implementation(path: Path, *, version: str | None) -> BenchmarkDefinition | None:
    benchmark_root = path.parent
    metadata = _parse_benchmark_metadata(benchmark_root / "bench.yaml")
    definition = _parse_spec_implementation(path, benchmark_root.name, metadata)
    if definition is None:
        return None
    if not _implementation_matches_version(definition, version):
        return None
    return definition


def _parse_benchmark_metadata(path: Path) -> dict[str, object]:
    if not path.exists():
        raise BenchmarkError(f"{path}: missing benchmark manifest")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise BenchmarkError(f"Failed to parse YAML benchmark manifest in {path}: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("bench"), dict):
        payload = payload["bench"]
    if not isinstance(payload, dict):
        raise BenchmarkError(f"{path}: benchmark manifest must be a mapping")
    return payload


def _parse_spec_implementation(
    path: Path,
    benchmark_id: str,
    metadata: dict[str, object],
) -> BenchmarkDefinition | None:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(raw_text, path)
    try:
        payload = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as exc:
        raise BenchmarkError(f"Failed to parse YAML frontmatter in {path}: {exc}") from exc
    implementation = payload.get("bench")
    if not isinstance(implementation, dict):
        raise BenchmarkError(f"{path}: missing 'bench' mapping in frontmatter")
    implementation_id = _require_str(implementation, "id", path, prefix="bench")
    description = _optional_str(implementation, "description", path, prefix="bench")
    min_version = _optional_str(implementation, "min_version", path, prefix="bench")
    max_version = _optional_str(implementation, "max_version", path, prefix="bench")
    tenzir_args = _parse_string_list(implementation.get("tenzir_args") or [], path, "bench.tenzir_args")
    tags = _merge_tags(
        _parse_tags(metadata.get("tags") or {}, path, "bench.yaml tags"),
        _parse_tags(implementation.get("tags") or {}, path, "bench.tags"),
    )
    input_section = metadata.get("input")
    if not isinstance(input_section, dict):
        raise BenchmarkError(f"{path}: bench.yaml input must be a mapping")
    input_path = _require_str(input_section, "path", path, prefix="input")
    input_source = _optional_str(input_section, "source", path, prefix="input")
    input_events = input_section.get("events")
    if input_events is not None and not isinstance(input_events, int):
        raise BenchmarkError(f"{path}: input.events must be an integer")
    input_measure = bool(input_section.get("measure", True))
    output_section = metadata.get("output")
    output_path: str | None = None
    output_measure = False
    if output_section is not None:
        if not isinstance(output_section, dict):
            raise BenchmarkError(f"{path}: bench.yaml output must be a mapping")
        output_path = output_section.get("path")
        if output_path is not None and not isinstance(output_path, str):
            raise BenchmarkError(f"{path}: output.path must be a string")
        output_measure = bool(output_section.get("measure", False))
    if input_measure and output_measure:
        raise BenchmarkError(f"{path}: only one of input.measure or output.measure can be true")
    if not input_measure and not output_measure:
        raise BenchmarkError(f"{path}: one of input.measure or output.measure must be true")
    if output_measure and output_path is None:
        raise BenchmarkError(f"{path}: output.path is required when output.measure is true")
    env = _parse_mapping_str(metadata.get("env") or {}, path, "bench.yaml env")
    fixtures = _parse_fixture_specs(metadata, path)
    runner = metadata.get("runner", "time")
    if not isinstance(runner, str):
        raise BenchmarkError(f"{path}: bench.yaml runner must be a string")
    runtime = _parse_runtime(metadata.get("runtime") or {}, path)
    implementation_description = description or _optional_str(metadata, "description", path, prefix="bench.yaml")
    return BenchmarkDefinition(
        path=path,
        id=f"{benchmark_id}/{implementation_id}",
        description=implementation_description,
        tags=tags,
        min_version=min_version,
        max_version=max_version,
        input_path=input_path,
        input_source=input_source,
        input_events=input_events,
        input_measure=input_measure,
        output_path=output_path,
        output_measure=output_measure,
        env=env,
        fixtures=fixtures,
        tenzir_args=tenzir_args,
        runner=runner,
        runtime=runtime,
        pipeline_body=body,
        benchmark_id=benchmark_id,
        implementation_id=implementation_id,
    )


def _implementation_matches_version(definition: BenchmarkDefinition, version: str | None) -> bool:
    if version is None:
        return True
    actual = _expand_version(version, upper=False)
    if actual is None:
        return True
    lower = _expand_version(definition.min_version, upper=False)
    upper = _expand_version(definition.max_version, upper=True)
    if lower is not None and actual < lower:
        return False
    if upper is not None and actual > upper:
        return False
    return True


def _expand_version(value: str | None, *, upper: bool) -> tuple[int, int, int] | None:
    if value is None:
        return None
    match = _VERSION_RE.match(value.strip())
    if not match:
        return None
    parts = match.group("body").split(".")
    expanded: list[int] = []
    wildcard = False
    for part in parts:
        if part.lower() in {"x", "*"}:
            wildcard = True
            expanded.append(_MAX_SENTINEL if upper else 0)
        else:
            expanded.append(int(part))
    while len(expanded) < 3:
        expanded.append(_MAX_SENTINEL if upper and wildcard else 0)
    return expanded[0], expanded[1], expanded[2]


def _merge_tags(shared: dict[str, str], implementation: dict[str, str]) -> dict[str, str]:
    merged = dict(shared)
    merged.update(implementation)
    return merged


def _parse_tags(value: object, path: Path, field_name: str) -> dict[str, str]:
    if isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return dict(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return {item: "" for item in value}
    if value in ({}, [], None):
        return {}
    raise BenchmarkError(f"{path}: {field_name} must be a mapping of strings or a list of strings")


def _parse_string_list(value: object, path: Path, field_name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BenchmarkError(f"{path}: {field_name} must be a list of strings")
    return list(value)


def _parse_mapping_str(value: object, path: Path, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise BenchmarkError(f"{path}: {field_name} must be a mapping of strings")
    return dict(value)


def _parse_runtime(value: object, path: Path) -> BenchmarkRuntime:
    if not isinstance(value, dict):
        raise BenchmarkError(f"{path}: bench.yaml runtime must be a mapping")
    warmup_runs = value.get("warmup_runs", 0)
    measurement_runs = value.get("measurement_runs", 1)
    timeout_seconds = value.get("timeout_seconds")
    if not isinstance(warmup_runs, int) or warmup_runs < 0:
        raise BenchmarkError(f"{path}: runtime.warmup_runs must be a non-negative integer")
    if not isinstance(measurement_runs, int) or measurement_runs <= 0:
        raise BenchmarkError(f"{path}: runtime.measurement_runs must be a positive integer")
    if timeout_seconds is not None and (not isinstance(timeout_seconds, int) or timeout_seconds <= 0):
        raise BenchmarkError(f"{path}: runtime.timeout_seconds must be a positive integer")
    return BenchmarkRuntime(
        warmup_runs=warmup_runs,
        measurement_runs=measurement_runs,
        timeout_seconds=timeout_seconds,
    )


def _require_str(mapping: dict[str, object], key: str, path: Path, *, prefix: str) -> str:
    value = mapping.get(key)
    if value is None:
        raise BenchmarkError(f"{path}: missing required key {prefix}.{key}")
    if not isinstance(value, str):
        raise BenchmarkError(f"{path}: {prefix}.{key} must be a string")
    return value


def _optional_str(mapping: dict[str, object], key: str, path: Path, *, prefix: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise BenchmarkError(f"{path}: {prefix}.{key} must be a string")
    return value


def _is_spec_root(path: Path) -> bool:
    return path.is_dir() and (path / "bench.yaml").exists()


def _is_spec_implementation_file(path: Path) -> bool:
    return path.is_file() and path.suffix == ".tql" and (path.parent / "bench.yaml").exists()
