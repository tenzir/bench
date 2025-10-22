"""Helpers for comparing builds."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .evaluation import evaluate as evaluate_results
from .executor import BenchmarkExecutor
from .paths import BenchPaths
from .runners import RunnerRegistry
from .reports import load_reports, select_fastest

_LOG = logging.getLogger(__name__)


def resolve_binaries(paths: Sequence[str]) -> List[Tuple[Path, bool]]:
    resolved: List[Tuple[Path, bool]] = []
    force = False
    for item in paths:
        if item == "--run":
            force = True
            continue
        path = Path(item)
        if path.is_dir():
            candidate = path / "bin" / "tenzir"
        else:
            candidate = path
        if not candidate.exists():
            raise FileNotFoundError(f"No tenzir executable at {candidate}")
        resolved.append((candidate.resolve(), force))
        force = False
    if len(resolved) < 2:
        raise ValueError("compare requires at least two binaries")
    return resolved


def run_compare(paths: BenchPaths, binaries: List[Tuple[Path, bool]], compact: bool) -> None:
    registry = RunnerRegistry()
    baseline_bin, baseline_force = binaries[0]
    baseline_executor = BenchmarkExecutor(paths, baseline_bin, registry)
    contexts = list(baseline_executor.discover(pattern=None))
    if not contexts:
        _LOG.error("No benchmarks found to execute")
        return

    compare_root = paths.results_state_dir / "compare"
    shutil.rmtree(compare_root, ignore_errors=True)
    compare_root.mkdir(parents=True, exist_ok=True)

    baseline_dir = compare_root / baseline_bin.name
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_reports = _ensure_reports(baseline_executor, contexts, baseline_dir, baseline_force)

    candidate_dirs = []
    for candidate_bin, candidate_force in binaries[1:]:
        candidate_executor = BenchmarkExecutor(paths, candidate_bin, registry)
        candidate_dir = compare_root / candidate_bin.name
        candidate_dir.mkdir(parents=True, exist_ok=True)
        _ensure_reports(candidate_executor, contexts, candidate_dir, candidate_force)
        candidate_dirs.append(candidate_dir)

    for candidate_dir in candidate_dirs:
        evaluate_results(paths, candidate_dir, baseline_dir, compact)


def _ensure_reports(
    executor: BenchmarkExecutor,
    contexts: Iterable,
    output_dir: Path,
    force: bool,
) -> Path:
    if not force:
        existing = select_fastest(load_reports(output_dir)) if output_dir.exists() else {}
        if existing:
            _LOG.info("Reusing cached reports in %s", output_dir)
            return output_dir
        cache_dir = executor.paths.results_cache_dir
        cache_reports = select_fastest(load_reports(cache_dir / executor.tenzir_bin.name if cache_dir else cache_dir))
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    reports_generated = []
    for context in contexts:
        generated = executor.execute(context)
        reports_generated.extend(filter(None, generated))
    if not reports_generated:
        return output_dir
    for report in reports_generated:
        report = report  # type: ignore[assignment]
        target = output_dir / report.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(report, target)
    return output_dir
