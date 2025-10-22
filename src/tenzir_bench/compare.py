"""Helpers for comparing builds."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .definitions import BenchmarkError, parse_benchmark_file
from .evaluation import evaluate as evaluate_results
from .executor import BenchmarkExecutor
from .paths import BenchPaths
from .runners import RunnerRegistry
from .reports import load_reports, select_fastest
from .definitions import parse_benchmark_file, BenchmarkError

_LOG = logging.getLogger(__name__)


def resolve_binaries(entries: Sequence[Tuple[str, bool]]) -> List[Tuple[Path, bool]]:
    resolved: List[Tuple[Path, bool]] = []
    for value, force in entries:
        path = Path(value)
        if path.is_dir():
            candidate = path / "bin" / "tenzir"
        else:
            candidate = path
        if not candidate.exists():
            raise FileNotFoundError(f"No tenzir executable at {candidate}")
        resolved.append((candidate.resolve(), force))
    if len(resolved) < 2:
        raise ValueError("compare requires at least two binaries")
    return resolved


def run_compare(
    paths: BenchPaths,
    binaries: List[Tuple[Path, bool]],
    compact: bool,
    benchmark_dirs: Sequence[Path],
) -> None:
    registry = RunnerRegistry()
    baseline_bin, baseline_force = binaries[0]
    baseline_executor = BenchmarkExecutor(paths, baseline_bin, registry)
    contexts = list(_discover_from_dirs(baseline_executor, benchmark_dirs))
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
    build = executor._get_build_info()  # type: ignore[attr-defined]
    if not force:
        if output_dir.exists() and any(output_dir.rglob("*.json")):
            _LOG.info("Reusing cached reports in %s", output_dir)
            return output_dir
        collected: List[Path] = []
        for context in contexts:
            run_dir = executor._result_dir(context, build)  # type: ignore[attr-defined]
            collected.extend(run_dir.glob("*.json"))
        if collected:
            _LOG.info("Reusing cached reports from state cache for %s", executor.tenzir_bin)
            shutil.rmtree(output_dir, ignore_errors=True)
            output_dir.mkdir(parents=True, exist_ok=True)
            for report in collected:
                target = output_dir / report.name
                shutil.copy2(report, target)
            return output_dir
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


def _discover_from_dirs(executor: BenchmarkExecutor, dirs: Sequence[Path]) -> Iterable:
    if not dirs:
        yield from executor.discover(pattern=None)
        return
    seen = set()
    for directory in dirs:
        if directory.is_file() and directory.suffix == ".tql":
            candidates = [directory]
        elif directory.is_dir():
            candidates = directory.rglob("*.tql")
        else:
            _LOG.warning("Benchmark path %s does not exist", directory)
            continue
        for file in candidates:
            if file in seen:
                continue
            seen.add(file)
            try:
                definition = parse_benchmark_file(file)
            except BenchmarkError as exc:
                _LOG.error("Skipping %s: %s", file, exc)
                continue
            context = executor.create_context(definition)
            if context:
                yield context
