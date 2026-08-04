"""Discovery and parsing of benchmark specs under ``bench/``."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
import re
from typing import cast

import yaml

from tenzir_bench.definitions import (
    BenchmarkDefinition,
    BenchmarkError,
    BenchmarkRuntime,
    parse_inputs,
    parse_fixture_specs,
    split_frontmatter,
)

_VERSION_RE = re.compile(r"^v?(?P<body>[0-9xX*]+(?:\.[0-9xX*]+){0,2})")
_MAX_SENTINEL = 999_999


@dataclass(frozen=True)
class Variant:
    """One parameterization of a single implementation file.

    A variant runs the same pipeline body again with additional Tenzir
    arguments, environment variables, and tags. Variants keep their own
    implementation id, so reports never collide.
    """

    id: str
    description: str | None = None
    tenzir_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    tags: Mapping[str, str] = field(default_factory=lambda: dict[str, str]())
    min_version: str | None = None
    max_version: str | None = None


def discover_definitions(
    pattern: str | None,
    *,
    version_supplier: Callable[[], str | None],
    root: Path | None = None,
    variants: Sequence[str] | None = None,
) -> list[BenchmarkDefinition]:
    resolved_root = (root or Path.cwd()).resolve()
    if pattern:
        candidate = Path(pattern)
        if candidate.exists():
            return load_definitions_from_paths(
                [candidate.resolve()],
                version_supplier=version_supplier,
                root=resolved_root,
                variants=variants,
            )
    bench_root = resolved_root / "bench"
    if bench_root.exists():
        return filter_variants(
            _load_bench_root(
                bench_root,
                patterns=[pattern] if pattern else None,
                version_supplier=version_supplier,
            ),
            variants,
        )
    return filter_variants(
        _discover_example_definitions(resolved_root, pattern, version_supplier=version_supplier),
        variants,
    )


def load_definitions_from_paths(
    paths: Sequence[Path],
    *,
    version_supplier: Callable[[], str | None],
    root: Path | None = None,
    variants: Sequence[str] | None = None,
) -> list[BenchmarkDefinition]:
    resolved_root = (root or Path.cwd()).resolve()
    seen: set[Path] = set()
    definitions: list[BenchmarkDefinition] = []
    pending_spec_paths: list[Path] = []
    for entry in paths:
        resolved = entry.resolve() if entry.is_absolute() else (resolved_root / entry).resolve()
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
            ),
        )
    return filter_variants(definitions, variants)


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
    return _load_spec_entries(selected, version=version_supplier())


def _discover_example_definitions(
    root: Path,
    pattern: str | None,
    *,
    version_supplier: Callable[[], str | None],
) -> list[BenchmarkDefinition]:
    examples_root = root / "examples" / "benchmarks"
    if pattern:
        candidate = Path(pattern)
        if candidate.exists():
            return load_definitions_from_paths(
                [candidate.resolve()],
                version_supplier=version_supplier,
                root=root,
            )
    if not examples_root.exists():
        return []
    selected = _select_benchmark_dirs(examples_root, [pattern] if pattern else None)
    return _load_spec_entries(selected, version=version_supplier())


def _load_legacy_definition(path: Path) -> BenchmarkDefinition:
    from tenzir_bench.definitions import parse_benchmark_file

    return parse_benchmark_file(path)


def _select_benchmark_dirs(bench_root: Path, patterns: Sequence[str] | None) -> list[Path]:
    benchmarks_root = bench_root / "benchmarks"
    if not benchmarks_root.exists():
        benchmarks_root = bench_root
    benchmark_dirs = sorted(
        directory
        for directory in benchmarks_root.iterdir()
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
) -> list[BenchmarkDefinition]:
    definitions: list[BenchmarkDefinition] = []
    loaded_roots: set[Path] = set()
    seen_impls: set[Path] = set()
    for entry in entries:
        resolved = entry.resolve()
        if _is_spec_implementation_file(resolved):
            if resolved not in seen_impls:
                definitions.extend(_load_spec_implementation(resolved, version=version))
                seen_impls.add(resolved)
            continue
        benchmark_root = resolved if resolved.is_dir() else resolved.parent
        if benchmark_root in loaded_roots:
            continue
        loaded_roots.add(benchmark_root)
        definitions.extend(_load_benchmark_dir(benchmark_root, version=version))
    definitions.sort(
        key=lambda definition: (
            definition.benchmark_id or definition.id,
            definition.implementation_id or definition.id,
        )
    )
    return definitions


def filter_variants(
    definitions: Sequence[BenchmarkDefinition],
    patterns: Sequence[str] | None,
) -> list[BenchmarkDefinition]:
    """Keep definitions whose variant id matches one of the glob patterns.

    Implementations without variants are always kept: the patterns select among
    the variants of an implementation, they do not exclude implementations that
    have none.
    """
    if not patterns:
        return list(definitions)
    return [
        definition
        for definition in definitions
        if definition.variant_id is None
        or any(fnmatch(definition.variant_id, pattern) for pattern in patterns)
    ]


def _load_benchmark_dir(directory: Path, *, version: str | None) -> list[BenchmarkDefinition]:
    metadata = _parse_benchmark_metadata(directory / "bench.yaml")
    definitions: list[BenchmarkDefinition] = []
    for file in sorted(directory.glob("*.tql")):
        for definition in _parse_spec_implementations(file, directory.name, metadata):
            if _implementation_matches_version(definition, version):
                definitions.append(definition)
    return definitions


def _load_spec_implementation(path: Path, *, version: str | None) -> list[BenchmarkDefinition]:
    benchmark_root = path.parent
    metadata = _parse_benchmark_metadata(benchmark_root / "bench.yaml")
    return [
        definition
        for definition in _parse_spec_implementations(path, benchmark_root.name, metadata)
        if _implementation_matches_version(definition, version)
    ]


def _parse_benchmark_metadata(path: Path) -> dict[str, object]:
    if not path.exists():
        raise BenchmarkError(f"{path}: missing benchmark manifest")
    payload = _load_yaml_mapping(path.read_text(encoding="utf-8"), path, "benchmark manifest")
    bench_section = payload.get("bench")
    if isinstance(bench_section, Mapping):
        payload = _string_key_mapping(cast(Mapping[object, object], bench_section), path, "bench")
    return payload


def _parse_spec_implementations(
    path: Path,
    benchmark_id: str,
    metadata: dict[str, object],
) -> list[BenchmarkDefinition]:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(raw_text, path)
    payload = _load_yaml_mapping(frontmatter, path, "frontmatter")
    implementation = _require_mapping(payload.get("bench"), path, "bench")
    implementation_id = _require_str(implementation, "id", path, prefix="bench")
    description = _optional_str(implementation, "description", path, prefix="bench")
    min_version = _optional_str(implementation, "min_version", path, prefix="bench")
    max_version = _optional_str(implementation, "max_version", path, prefix="bench")
    tenzir_args = _parse_string_list(
        implementation.get("tenzir_args") or [], path, "bench.tenzir_args"
    )
    tags = _merge_tags(
        _parse_tags(metadata.get("tags") or {}, path, "bench.yaml tags"),
        _parse_tags(implementation.get("tags") or {}, path, "bench.tags"),
    )
    inputs = parse_inputs(metadata, path, prefix="bench.yaml")
    output_section = metadata.get("output")
    output_path: str | None = None
    if output_section is not None:
        output_section = _require_mapping(output_section, path, "output")
        output_path = _optional_str(output_section, "path", path, prefix="output")
    if output_section is not None and output_path is None:
        raise BenchmarkError(f"{path}: output.path must be a string")
    env = _parse_mapping_str(metadata.get("env") or {}, path, "bench.yaml env")
    fixtures = parse_fixture_specs(metadata, path)
    runner = metadata.get("runner", "time")
    if not isinstance(runner, str):
        raise BenchmarkError(f"{path}: bench.yaml runner must be a string")
    runtime = _parse_runtime(metadata.get("runtime") or {}, path)
    implementation_description = description or _optional_str(
        metadata, "description", path, prefix="bench.yaml"
    )
    variants = _parse_variants(implementation.get("variants"), path, "bench.variants")
    if variants is None:
        variants = _parse_variants(metadata.get("variants"), path, "bench.yaml variants")
    if not variants:
        variants = [Variant(id="")]
    definitions: list[BenchmarkDefinition] = []
    for variant in variants:
        variant_implementation_id = (
            f"{implementation_id}/{variant.id}" if variant.id else implementation_id
        )
        definitions.append(
            BenchmarkDefinition(
                path=path,
                id=f"{benchmark_id}/{variant_implementation_id}",
                description=variant.description or implementation_description,
                tags=_merge_tags(tags, dict(variant.tags)),
                min_version=variant.min_version or min_version,
                max_version=variant.max_version or max_version,
                inputs=inputs,
                output_path=output_path,
                env={**env, **dict(variant.env)},
                fixtures=fixtures,
                tenzir_args=[*tenzir_args, *variant.tenzir_args],
                runner=runner,
                runtime=runtime,
                pipeline_body=body,
                benchmark_id=benchmark_id,
                implementation_id=variant_implementation_id,
                variant_id=variant.id or None,
            )
        )
    return definitions


def _parse_variants(value: object, path: Path, label: str) -> list[Variant] | None:
    """Parse a `variants:` section into an ordered list of variants.

    Returns `None` when the section is absent, so callers can fall back to the
    variants declared in `bench.yaml`.
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{path}: {label} must be a mapping of variant ids")
    variants: list[Variant] = []
    for variant_id, raw in _string_key_mapping(
        cast(Mapping[object, object], value), path, label
    ).items():
        if not variant_id:
            raise BenchmarkError(f"{path}: {label} ids must not be empty")
        options = _require_mapping(raw or {}, path, f"{label}.{variant_id}")
        unknown = set(options) - {
            "description",
            "tenzir_args",
            "env",
            "tags",
            "min_version",
            "max_version",
        }
        if unknown:
            keys = ", ".join(sorted(unknown))
            raise BenchmarkError(f"{path}: {label}.{variant_id} has unknown keys: {keys}")
        variants.append(
            Variant(
                id=variant_id,
                description=_optional_str(options, "description", path, prefix=label),
                tenzir_args=tuple(
                    _parse_string_list(
                        options.get("tenzir_args") or [],
                        path,
                        f"{label}.{variant_id}.tenzir_args",
                    )
                ),
                env=_parse_mapping_str(options.get("env") or {}, path, f"{label}.{variant_id}.env"),
                tags=_parse_tags(options.get("tags") or {}, path, f"{label}.{variant_id}.tags"),
                min_version=_optional_str(options, "min_version", path, prefix=label),
                max_version=_optional_str(options, "max_version", path, prefix=label),
            )
        )
    return variants


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


def _load_yaml_mapping(text: str, path: Path, label: str) -> dict[str, object]:
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
    path: Path,
    label: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            raise BenchmarkError(f"{path}: {label} keys must be strings")
        result[key] = entry
    return result


def _require_mapping(value: object, path: Path, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{path}: {label} must be a mapping")
    return _string_key_mapping(cast(Mapping[object, object], value), path, label)


def _parse_tags(value: object, path: Path, field_name: str) -> dict[str, str]:
    if isinstance(value, Mapping):
        result: dict[str, str] = {}
        for key, entry in cast(Mapping[object, object], value).items():
            if not isinstance(key, str) or not isinstance(entry, str):
                break
            result[key] = entry
        else:
            return result
    if isinstance(value, list):
        result = {}
        for item in cast(list[object], value):
            if not isinstance(item, str):
                break
            result[item] = ""
        else:
            return result
    if value in ({}, [], None):
        return {}
    raise BenchmarkError(f"{path}: {field_name} must be a mapping of strings or a list of strings")


def _parse_string_list(value: object, path: Path, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise BenchmarkError(f"{path}: {field_name} must be a list of strings")
    result: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str):
            raise BenchmarkError(f"{path}: {field_name} must be a list of strings")
        result.append(item)
    return result


def _parse_mapping_str(value: object, path: Path, field_name: str) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{path}: {field_name} must be a mapping of strings")
    result: dict[str, str] = {}
    for key, entry in cast(Mapping[object, object], value).items():
        if not isinstance(key, str) or not isinstance(entry, str):
            raise BenchmarkError(f"{path}: {field_name} must be a mapping of strings")
        result[key] = entry
    return result


def _parse_runtime(value: object, path: Path) -> BenchmarkRuntime:
    if not isinstance(value, Mapping):
        raise BenchmarkError(f"{path}: bench.yaml runtime must be a mapping")
    value = _string_key_mapping(cast(Mapping[object, object], value), path, "runtime")
    warmup_runs = value.get("warmup_runs", 0)
    measurement_runs = value.get("measurement_runs", 1)
    timeout_seconds = value.get("timeout_seconds")
    if not isinstance(warmup_runs, int) or warmup_runs < 0:
        raise BenchmarkError(f"{path}: runtime.warmup_runs must be a non-negative integer")
    if not isinstance(measurement_runs, int) or measurement_runs <= 0:
        raise BenchmarkError(f"{path}: runtime.measurement_runs must be a positive integer")
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, int) or timeout_seconds <= 0
    ):
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
