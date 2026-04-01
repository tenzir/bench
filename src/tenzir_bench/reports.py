"""Utilities for loading benchmark reports."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_COMMITISH_RE = re.compile(r"[0-9a-fA-F]{7,40}")


@dataclass
class Report:
    path: Path
    pipeline: str
    benchmark_id: str
    implementation_id: str | None
    target: str | None
    hardware_key: str | None
    wall_clock: float
    rss_kb: int
    build_version: str | None
    artifact_id: str | None


def load_report(path: Path, *, artifact_id: str | None = None) -> Report | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return parse_report_payload(payload, path=path, artifact_id=artifact_id)


def parse_report_payload(
    payload: dict[str, Any],
    *,
    path: Path,
    artifact_id: str | None = None,
) -> Report | None:
    pipeline = payload.get("pipeline")
    if not pipeline:
        return None
    build = payload.get("build", {})
    hardware = payload.get("hardware", {})
    version = build.get("version")
    target = payload.get("target")
    hardware_key = hardware.get("key") if isinstance(hardware, dict) else None
    runtime = payload.get("runtime", {})
    wall_clock = runtime.get("wall_clock")
    rss = runtime.get("max_resident_set_kb")
    if wall_clock is None or rss is None:
        return None
    return Report(
        path=path,
        pipeline=pipeline,
        benchmark_id=str(payload.get("benchmark_id") or pipeline),
        implementation_id=payload.get("implementation_id"),
        target=target if isinstance(target, str) else None,
        hardware_key=hardware_key if isinstance(hardware_key, str) else None,
        wall_clock=float(wall_clock),
        rss_kb=int(rss),
        build_version=version,
        artifact_id=artifact_id,
    )


def load_reports(directory: Path, artifact_filter: str | None = None) -> dict[str, list[Report]]:
    results: dict[str, list[Report]] = {}
    if not directory.exists():
        return results
    for file in directory.rglob("*.json"):
        artifact_id = _artifact_id(directory, file)
        if artifact_filter and not matches_identifier(artifact_id, artifact_filter):
            continue
        report = load_report(file, artifact_id=artifact_id)
        if report is None:
            continue
        results.setdefault(report.pipeline, []).append(report)
    return results


def select_fastest(reports: dict[str, list[Report]]) -> dict[str, Report]:
    fastest: dict[str, Report] = {}
    for pipeline, items in reports.items():
        if not items:
            continue
        fastest[pipeline] = min(items, key=lambda r: r.wall_clock)
    return fastest


def _trim_prefix(version: str | None) -> str | None:
    if version and version.startswith("v"):
        return version[1:]
    return version


def matches_identifier(actual: str | None, expected: str | None) -> bool:
    if expected is None:
        return True
    if actual is None:
        return False
    actual_variants = {value for value in (actual, _trim_prefix(actual)) if value}
    expected_variants = {value for value in (expected, _trim_prefix(expected)) if value}
    if actual_variants & expected_variants:
        return True
    if any(
        _commitish_matches(actual_variant, expected_variant)
        for actual_variant in actual_variants
        for expected_variant in expected_variants
    ):
        return True
    return False


def _artifact_id(directory: Path, file: Path) -> str | None:
    try:
        relative = file.relative_to(directory)
    except ValueError:
        return None
    if len(relative.parts) < 5:
        return None
    return relative.parts[-3]


def _commitish_matches(actual: str, expected: str) -> bool:
    if not _looks_like_commitish(expected):
        return False
    actual_tokens = _COMMITISH_RE.findall(actual)
    for token in actual_tokens:
        if expected.startswith(token) or token.startswith(expected):
            return True
    return False


def _looks_like_commitish(value: str) -> bool:
    return len(value) >= 7 and all(char in "0123456789abcdefABCDEF" for char in value)
