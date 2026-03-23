"""Evaluation of benchmark results."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .metadata import GitHubMetadata
from .paths import BenchPaths
from .reports import load_reports, select_fastest

_LOG = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    pipeline: str
    candidate: float | None
    baseline: float | None
    delta: float | None
    candidate_rss: int | None
    baseline_rss: int | None
    rss_delta: int | None


def evaluate(paths: BenchPaths, runs_dir: Path, base_dir: Path | None, compact: bool) -> None:
    metadata = GitHubMetadata(paths.metadata_cache_dir)
    release_tag = _latest_release(metadata)
    main_commit = _latest_main(metadata)

    candidate_reports = select_fastest(load_reports(runs_dir))
    baseline_reports = {}
    if base_dir:
        baseline_reports = select_fastest(load_reports(base_dir))
    elif release_tag:
        baseline_reports = select_fastest(load_reports(paths.results_cache_dir, artifact_filter=release_tag))
    else:
        _LOG.warning("Unable to determine latest release tag; skipping release baseline")

    main_reports = {}
    if main_commit:
        main_reports = select_fastest(load_reports(paths.results_cache_dir, artifact_filter=main_commit))
        if not main_reports:
            _LOG.warning("No cached main branch results found for %s", main_commit)
    else:
        _LOG.warning("Unable to determine latest main commit; skipping main comparison")

    pipelines = sorted(set(candidate_reports) | set(baseline_reports) | set(main_reports))
    if not pipelines:
        _LOG.error("No reports found to evaluate")
        return

    if compact:
        _print_compact_table(pipelines, candidate_reports, baseline_reports, main_reports)
    else:
        _print_detailed(pipelines, candidate_reports, baseline_reports, main_reports)


def _latest_release(metadata: GitHubMetadata) -> str | None:
    releases = metadata.fetch_releases()
    if not releases:
        _LOG.warning("No releases found when querying GitHub")
        return None
    return releases[0]["tag"]


def _latest_main(metadata: GitHubMetadata) -> str | None:
    commits = metadata.fetch_main_commits()
    if not commits:
        return None
    return commits[0]["sha"]


def _print_compact_table(
    pipelines,
    candidate_reports,
    baseline_reports,
    main_reports,
) -> None:
    header = (
        f"{'pipeline':40} {'base(s)':>10} {'cand(s)':>10} {'Δbase(s)':>10} {'Δbase(%)':>10}"
        f" {'main(s)':>10} {'Δmain(s)':>10} {'Δmain(%)':>10}"
    )
    print(header)
    print("-" * len(header))
    for pipeline in pipelines:
        cand = candidate_reports.get(pipeline)
        base = baseline_reports.get(pipeline)
        main = main_reports.get(pipeline)
        base_val = f"{base.wall_clock:.2f}" if base else "-"
        cand_val = f"{cand.wall_clock:.2f}" if cand else "-"
        delta_base = _format_delta(cand, base)
        delta_base_pct = _format_percent(cand, base)
        main_val = f"{main.wall_clock:.2f}" if main else "-"
        delta_main = _format_delta(cand, main)
        delta_main_pct = _format_percent(cand, main)
        print(
            f"{pipeline:40} {base_val:>10} {cand_val:>10} {delta_base:>10} {delta_base_pct:>10}"
            f" {main_val:>10} {delta_main:>10} {delta_main_pct:>10}",
        )


def _print_detailed(pipelines, candidate_reports, baseline_reports, main_reports) -> None:
    for pipeline in pipelines:
        print(f"Pipeline: {pipeline}")
        cand = candidate_reports.get(pipeline)
        base = baseline_reports.get(pipeline)
        main = main_reports.get(pipeline)
        if cand:
            print(f"  Candidate: wall={cand.wall_clock:.2f}s rss={cand.rss_kb}k")
        else:
            print("  Candidate: missing")
        if base:
            print(
                f"  Baseline:  wall={base.wall_clock:.2f}s rss={base.rss_kb}k "
                f"Δ={_format_delta_detail(cand, base)} ({_format_percent_detail(cand, base)}) "
                f"Δrss={_format_rss_delta(cand, base)}",
            )
        if main:
            print(
                f"  Main:      wall={main.wall_clock:.2f}s rss={main.rss_kb}k "
                f"Δ={_format_delta_detail(cand, main)} ({_format_percent_detail(cand, main)}) "
                f"Δrss={_format_rss_delta(cand, main)}",
            )
        print()


def _format_delta(candidate, reference) -> str:
    if not candidate or not reference:
        return "-"
    delta = candidate.wall_clock - reference.wall_clock
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.2f}"


def _delta(candidate, reference) -> float:
    if not candidate or not reference:
        return 0.0
    return candidate.wall_clock - reference.wall_clock


def _percent_delta(candidate, reference) -> float | None:
    if not candidate or not reference or reference.wall_clock == 0:
        return None
    return ((candidate.wall_clock - reference.wall_clock) / reference.wall_clock) * 100.0


def _format_percent(candidate, reference) -> str:
    pct = _percent_delta(candidate, reference)
    if pct is None:
        return "-"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}"


def _rss_delta(candidate, reference) -> int:
    if not candidate or not reference:
        return 0
    return (candidate.rss_kb or 0) - (reference.rss_kb or 0)


def _format_delta_detail(candidate, reference) -> str:
    if not candidate or not reference:
        return "-"
    delta = candidate.wall_clock - reference.wall_clock
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.2f}s"


def _format_percent_detail(candidate, reference) -> str:
    pct = _percent_delta(candidate, reference)
    if pct is None:
        return "-"
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _format_rss_delta(candidate, reference) -> str:
    if not candidate or not reference:
        return "-"
    delta = (candidate.rss_kb or 0) - (reference.rss_kb or 0)
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}k"
