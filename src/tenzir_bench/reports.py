"""Utilities for loading benchmark reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class Report:
    path: Path
    pipeline: str
    wall_clock: float
    rss_kb: int
    build_version: Optional[str]


def load_reports(directory: Path, version_filter: Optional[str] = None) -> Dict[str, List[Report]]:
    results: Dict[str, List[Report]] = {}
    if not directory.exists():
        return results
    for file in directory.rglob("*.json"):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        pipeline = payload.get("pipeline")
        if not pipeline:
            continue
        build = payload.get("build", {})
        version = build.get("version")
        if version_filter and version_filter not in {version, _trim_prefix(version)}:
            continue
        runtime = payload.get("runtime", {})
        wall_clock = runtime.get("wall_clock")
        rss = runtime.get("max_resident_set_kb")
        if wall_clock is None or rss is None:
            continue
        report = Report(
            path=file,
            pipeline=pipeline,
            wall_clock=float(wall_clock),
            rss_kb=int(rss),
            build_version=version,
        )
        results.setdefault(pipeline, []).append(report)
    return results


def select_fastest(reports: Dict[str, List[Report]]) -> Dict[str, Report]:
    fastest: Dict[str, Report] = {}
    for pipeline, items in reports.items():
        if not items:
            continue
        fastest[pipeline] = min(items, key=lambda r: r.wall_clock)
    return fastest


def _trim_prefix(version: Optional[str]) -> Optional[str]:
    if version and version.startswith("v"):
        return version[1:]
    return version
