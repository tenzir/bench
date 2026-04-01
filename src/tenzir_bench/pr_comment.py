"""Markdown rendering helpers for grouped PR benchmark comments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from tenzir_bench.reports import Report


def render_grouped_markdown(builds: Sequence[tuple[str, Mapping[str, Report]]]) -> str:
    if not builds:
        return ""
    benchmark_ids = sorted(
        {report.benchmark_id for _label, reports in builds for report in reports.values()},
    )
    sections: list[str] = []
    for benchmark_id in benchmark_ids:
        sections.append(f"## {benchmark_id}")
        sections.append("")
        sections.append("| Implementation | Build | Seconds | Δseconds | RSS | Δrss |")
        sections.append("| --- | --- | ---: | ---: | ---: | ---: |")
        implementations = _implementation_ids(builds, benchmark_id)
        for implementation_id in implementations:
            group = [
                (label, _report_for(reports, benchmark_id, implementation_id))
                for label, reports in builds
            ]
            present = [(label, report) for label, report in group if report is not None]
            if not present:
                continue
            baseline = present[0][1]
            assert baseline is not None
            for label, report in present:
                sections.append(
                    "| {implementation} | {label} | {seconds} | {delta_seconds} | {rss} | {delta_rss} |".format(
                        implementation=implementation_id,
                        label=label,
                        seconds=_fmt_seconds(report),
                        delta_seconds=_fmt_percent_change(baseline.wall_clock, report.wall_clock),
                        rss=_fmt_rss(report),
                        delta_rss=_fmt_percent_change(float(baseline.rss_kb), float(report.rss_kb)),
                    ),
                )
        sections.append("")
    return "\n".join(sections).strip() + "\n"


def _implementation_ids(
    builds: Sequence[tuple[str, Mapping[str, Report]]],
    benchmark_id: str,
) -> list[str]:
    implementations: list[str] = []
    seen: set[str] = set()
    for _label, reports in builds:
        for report in reports.values():
            if report.benchmark_id != benchmark_id:
                continue
            implementation_id = report.implementation_id or report.pipeline
            if implementation_id in seen:
                continue
            seen.add(implementation_id)
            implementations.append(implementation_id)
    return implementations


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


def _fmt_seconds(report: Report) -> str:
    return f"{report.wall_clock:.2f}"


def _fmt_rss(report: Report) -> str:
    return f"{report.rss_kb / 1024:.0f} MB"


def _fmt_percent_change(base: float, value: float) -> str:
    if base == 0:
        return "-"
    delta = ((value - base) / base) * 100
    if abs(delta) < 0.05:
        return "0.0%"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"
