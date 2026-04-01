"""Markdown rendering helpers for grouped PR benchmark comments."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from tenzir_bench.reports import Report

BuildRole = Literal["release", "main", "extra", "candidate"]
BuildInput: TypeAlias = "BuildDisplay | tuple[str, Mapping[str, Report]]"

_SEMVER_RE = re.compile(r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")


@dataclass(frozen=True)
class BuildDisplay:
    label: str
    reports: Mapping[str, Report]
    role: BuildRole
    target: str | None = None
    ref: str | None = None
    implicit: bool = False
    request_index: int | None = None


def render_grouped_markdown(builds: Sequence[BuildInput]) -> str:
    rendered_builds = _normalize_builds(builds)
    if not rendered_builds:
        return ""
    benchmark_ids = sorted(
        {report.benchmark_id for build in rendered_builds for report in build.reports.values()},
    )
    sections: list[str] = []
    for benchmark_id in benchmark_ids:
        sections.append(f"## {benchmark_id}")
        sections.append("")
        sections.append("| Implementation | Build | Seconds | Δseconds | RSS | Δrss |")
        sections.append("| --- | --- | ---: | ---: | ---: | ---: |")
        implementations = _implementation_ids(rendered_builds, benchmark_id)
        baseline_by_implementation = _baseline_reports(rendered_builds, benchmark_id)
        for implementation_id in implementations:
            baseline = baseline_by_implementation.get(implementation_id)
            present = _ordered_present_builds(rendered_builds, benchmark_id, implementation_id)
            if not present:
                continue
            for build, report in present:
                sections.append(
                    "| {implementation} | {label} | {seconds} | {delta_seconds} | {rss} | {delta_rss} |".format(
                        implementation=implementation_id,
                        label=build.label,
                        seconds=_fmt_seconds(report),
                        delta_seconds=_fmt_percent_change(
                            baseline.wall_clock if baseline else None,
                            report.wall_clock,
                        ),
                        rss=_fmt_rss(report),
                        delta_rss=_fmt_percent_change(
                            _rss_value(baseline),
                            _rss_value(report),
                        ),
                    ),
                )
        sections.append("")
    return "\n".join(sections).strip() + "\n"


def _normalize_builds(builds: Sequence[BuildInput]) -> list[BuildDisplay]:
    normalized: list[BuildDisplay] = []
    for index, build in enumerate(builds):
        if isinstance(build, BuildDisplay):
            normalized.append(
                BuildDisplay(
                    label=build.label,
                    reports=build.reports,
                    role=build.role,
                    target=build.target or _single_target(build.reports),
                    ref=build.ref,
                    implicit=build.implicit,
                    request_index=index if build.request_index is None else build.request_index,
                )
            )
            continue
        label, reports = build
        inferred = _infer_build_display(label, reports, index)
        normalized.append(inferred)
    return normalized


def _infer_build_display(
    label: str,
    reports: Mapping[str, Report],
    request_index: int,
) -> BuildDisplay:
    target = _infer_target_from_label(label) or _single_target(reports)
    trimmed = label.strip()
    if trimmed.startswith("candidate"):
        return BuildDisplay(
            label=label,
            reports=reports,
            role="candidate",
            target=target,
            implicit=True,
            request_index=request_index,
        )
    if trimmed.startswith("main"):
        return BuildDisplay(
            label=label,
            reports=reports,
            role="main",
            target=target,
            implicit=True,
            request_index=request_index,
        )
    if trimmed.startswith("latest stable"):
        return BuildDisplay(
            label=label,
            reports=reports,
            role="release",
            target=target,
            ref=_release_ref(trimmed, reports),
            implicit=True,
            request_index=request_index,
        )
    if "@" in trimmed:
        maybe_target, maybe_ref = trimmed.split("@", 1)
        if maybe_target in {"docker", "static"}:
            if _version_key(maybe_ref) is not None:
                return BuildDisplay(
                    label=label,
                    reports=reports,
                    role="release",
                    target=maybe_target,
                    ref=maybe_ref,
                    implicit=False,
                    request_index=request_index,
                )
            return BuildDisplay(
                label=label,
                reports=reports,
                role="extra",
                target=maybe_target,
                ref=maybe_ref,
                implicit=False,
                request_index=request_index,
            )
    return BuildDisplay(
        label=label,
        reports=reports,
        role="extra",
        target=target,
        implicit=False,
        request_index=request_index,
    )


def _release_ref(label: str, reports: Mapping[str, Report]) -> str | None:
    if match := _SEMVER_RE.search(label):
        return match.group(0)
    for report in reports.values():
        if report.build_version:
            return report.build_version
    return None


def _infer_target_from_label(label: str) -> str | None:
    trimmed = label.strip()
    if trimmed.startswith("candidate "):
        suffix = trimmed.removeprefix("candidate ").strip()
        if suffix in {"docker", "static"}:
            return suffix
    if trimmed.startswith("main "):
        suffix = trimmed.removeprefix("main ").strip()
        if suffix in {"docker", "static"}:
            return suffix
    if trimmed.startswith("latest stable "):
        suffix = trimmed.removeprefix("latest stable ").strip()
        if suffix in {"docker", "static"}:
            return suffix
    if "@" in trimmed:
        maybe_target, _maybe_ref = trimmed.split("@", 1)
        if maybe_target in {"docker", "static"}:
            return maybe_target
    return None


def _single_target(reports: Mapping[str, Report]) -> str | None:
    targets = {report.target for report in reports.values() if report.target}
    if len(targets) == 1:
        return next(iter(targets))
    return None


def _implementation_ids(
    builds: Sequence[BuildDisplay],
    benchmark_id: str,
) -> list[str]:
    implementations: list[str] = []
    seen: set[str] = set()
    for build in builds:
        for report in build.reports.values():
            if report.benchmark_id != benchmark_id:
                continue
            implementation_id = report.implementation_id or report.pipeline
            if implementation_id in seen:
                continue
            seen.add(implementation_id)
            implementations.append(implementation_id)
    return implementations


def _baseline_reports(
    builds: Sequence[BuildDisplay],
    benchmark_id: str,
) -> dict[str, Report]:
    baselines: dict[str, Report] = {}
    implementations = _implementation_ids(builds, benchmark_id)
    for implementation_id in implementations:
        baseline = _baseline_report(builds, benchmark_id, implementation_id)
        if baseline is not None:
            baselines[implementation_id] = baseline
    return baselines


def _baseline_report(
    builds: Sequence[BuildDisplay],
    benchmark_id: str,
    implementation_id: str,
) -> Report | None:
    docker_main = _report_for_role(
        builds, benchmark_id, implementation_id, role="main", target="docker"
    )
    if docker_main is not None:
        return docker_main
    return _report_for_role(builds, benchmark_id, implementation_id, role="main", target="static")


def _report_for_role(
    builds: Sequence[BuildDisplay],
    benchmark_id: str,
    implementation_id: str,
    *,
    role: BuildRole,
    target: str,
) -> Report | None:
    for build in builds:
        if build.role != role or not build.implicit or build.target != target:
            continue
        report = _report_for(build.reports, benchmark_id, implementation_id)
        if report is not None:
            return report
    return None


def _report_for(
    reports: Mapping[str, Report],
    benchmark_id: str,
    implementation_id: str,
) -> Report | None:
    for report in reports.values():
        report_implementation = report.implementation_id or report.pipeline
        if report.benchmark_id == benchmark_id and report_implementation == implementation_id:
            return report
    return None


def _build_order_key(build: BuildDisplay) -> tuple[int, tuple[int, int, int], int, int]:
    if build.role == "release":
        return (
            0,
            _version_key(build.ref) or (0, 0, 0),
            build.request_index or 0,
            _target_order(build.target),
        )
    if build.role == "main":
        return (1, (0, 0, 0), build.request_index or 0, _target_order(build.target))
    if build.role == "extra":
        return (2, (0, 0, 0), build.request_index or 0, _target_order(build.target))
    return (3, (0, 0, 0), build.request_index or 0, _target_order(build.target))


def _target_order(target: str | None) -> int:
    if target == "docker":
        return 0
    if target == "static":
        return 1
    return 2


def _version_key(version: str | None) -> tuple[int, int, int] | None:
    if version is None:
        return None
    trimmed = version.split("+", 1)[0]
    match = _SEMVER_RE.match(trimmed)
    if not match:
        return None
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )


def _fmt_seconds(report: Report) -> str:
    return f"{report.wall_clock:.2f}"


def _fmt_rss(report: Report) -> str:
    rss = _rss_value(report)
    if rss is None:
        return "n/a"
    return f"{rss / 1024:.0f} MB"


def _fmt_percent_change(base: float | None, value: float | None) -> str:
    if base in (None, 0) or value is None:
        return "-"
    delta = ((value - base) / base) * 100
    if abs(delta) < 0.05:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def _rss_value(report: Report | None) -> float | None:
    if report is None:
        return None
    return float(report.rss_kb)


def _ordered_present_builds(
    builds: Sequence[BuildDisplay],
    benchmark_id: str,
    implementation_id: str,
) -> list[tuple[BuildDisplay, Report]]:
    present = [
        (build, report)
        for build in builds
        if (report := _report_for(build.reports, benchmark_id, implementation_id)) is not None
    ]
    present.sort(key=lambda item: _build_order_key(item[0]))
    return present
