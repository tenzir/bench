"""Helpers for comparing builds."""

from __future__ import annotations

import hashlib
import logging
import re
import shlex
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path
from textwrap import dedent

from .definitions import BenchmarkError, parse_benchmark_file
from .executor import BenchmarkExecutor
from .paths import BenchPaths
from .reports import Report, load_reports, select_fastest
from .runners import RunnerRegistry

_LOG = logging.getLogger(__name__)


def resolve_binaries(paths: BenchPaths, entries: Sequence[tuple[str, bool]]) -> list[tuple[Path, bool]]:
    resolved: list[tuple[Path, bool]] = []
    for value, force in entries:
        resolved.append((_resolve_entry(paths, value), force))
    if len(resolved) < 2:
        raise ValueError("compare requires at least two binaries")
    return resolved


def _resolve_entry(paths: BenchPaths, value: str) -> Path:
    if value.startswith("docker://"):
        image = value[len("docker://") :].strip()
        if not image:
            raise ValueError("docker image reference must not be empty")
        return _ensure_docker_wrapper(paths, image)
    path = Path(value)
    if path.is_dir():
        candidate = path / "bin" / "tenzir"
    else:
        candidate = path
    if not candidate.exists():
        raise FileNotFoundError(f"No tenzir executable at {candidate}")
    return candidate.resolve()


def run_compare(
    paths: BenchPaths,
    binaries: list[tuple[Path, bool]],
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

    baseline_info = baseline_executor._get_build_info()  # type: ignore[attr-defined]
    baseline_label = baseline_info.build_id or _label(baseline_bin)
    baseline_dir = compare_root / baseline_label
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_reports = _ensure_reports(baseline_executor, contexts, baseline_dir, baseline_force)

    candidate_dirs = []
    for candidate_bin, candidate_force in binaries[1:]:
        candidate_executor = BenchmarkExecutor(paths, candidate_bin, registry)
        info = candidate_executor._get_build_info()  # type: ignore[attr-defined]
        label = info.build_id or _label(candidate_bin)
        candidate_dir = compare_root / label
        candidate_dir.mkdir(parents=True, exist_ok=True)
        _ensure_reports(candidate_executor, contexts, candidate_dir, candidate_force)
        candidate_dirs.append((label, candidate_dir))

    baseline_reports = select_fastest(load_reports(baseline_dir))
    candidate_reports = [(label, select_fastest(load_reports(directory))) for label, directory in candidate_dirs]
    _render_table(baseline_label, baseline_reports, candidate_reports)


def _ensure_reports(
    executor: BenchmarkExecutor,
    contexts: Iterable,
    output_dir: Path,
    force: bool,
) -> Path:
    contexts = list(contexts)
    build = executor._get_build_info()  # type: ignore[attr-defined]
    if not force:
        if output_dir.exists() and any(output_dir.rglob("*.json")):
            _LOG.info("Reusing cached reports in %s", output_dir)
            return output_dir
        collected: list[Path] = []
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
    if contexts:
        executor.prepare_progress(contexts)
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


def _render_table(
    baseline_label: str,
    baseline_reports: dict[str, Report],
    candidate_reports: Sequence[tuple[str, dict[str, Report]]],
) -> None:
    candidate_label_lengths = [len(label) for label, _ in candidate_reports]
    label_width = max([len("build"), len(baseline_label), *candidate_label_lengths])
    header = f"{'build':<{label_width}} {'seconds':>10} {'Δseconds':>12} {'rss':>10} {'Δrss':>12}"
    separator = "-" * len(header)
    pipelines = sorted(
        set(baseline_reports)
        | set().union(*(reports.keys() for _, reports in candidate_reports)),
    )
    for pipeline in pipelines:
        print(pipeline)
        print(separator)
        print(header)
        base = baseline_reports.get(pipeline)
        print(_format_row(baseline_label, base, None, label_width=label_width, show_delta=False))
        for label, reports in candidate_reports:
            cand = reports.get(pipeline)
            print(_format_row(label, cand, base, label_width=label_width, show_delta=True))
        print()


def _format_row(
    label: str,
    report: Report | None,
    baseline: Report | None,
    *,
    label_width: int,
    show_delta: bool,
) -> str:
    seconds = _fmt_seconds(report)
    rss = _fmt_rss(report)
    if show_delta:
        seconds_delta = _fmt_percent_change(
            baseline.wall_clock if baseline else None,
            report.wall_clock if report else None,
        )
        rss_delta = _fmt_percent_change(
            baseline.rss_kb if baseline else None,
            report.rss_kb if report else None,
        )
    else:
        seconds_delta = ""
        rss_delta = ""
    return (
        f"{label:<{label_width}} "
        f"{seconds:>10} {seconds_delta:>12} {rss:>10} {rss_delta:>12}"
    )


def _fmt_seconds(report: Report | None) -> str:
    return f"{report.wall_clock:.2f}" if report else "-"


def _fmt_rss(report: Report | None) -> str:
    if not report or report.rss_kb is None:
        return "-"
    return f"{report.rss_kb / 1024:.0f} MB"


def _fmt_percent_change(base: float | None, cand: float | None) -> str:
    if base is None or cand is None or base == 0:
        return "-"
    delta = ((cand - base) / base) * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def _label(path: Path) -> str:
    if path.name == "tenzir" and path.parent.name == "bin":
        return path.parent.parent.name
    return path.parent.name if path.is_file() else path.name


def _ensure_docker_wrapper(paths: BenchPaths, image: str) -> Path:
    wrapper_dir = paths.ensure_dir(paths.state_dir / "docker")
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", image)
    wrapper_path = wrapper_dir / f"{safe}-{digest}.sh"
    script = _docker_wrapper_script(image, paths)
    wrapper_path.write_text(script, encoding="utf-8")
    wrapper_path.chmod(0o755)
    return wrapper_path


def _docker_wrapper_script(image: str, paths: BenchPaths) -> str:
    cache_dir = shlex.quote(str(paths.cache_dir))
    state_dir = shlex.quote(str(paths.state_dir))
    work_dir = shlex.quote(str(Path.cwd()))
    image_ref = shlex.quote(image)
    return dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail

        IMAGE={image_ref}
        CACHE_DIR={cache_dir}
        STATE_DIR={state_dir}
        WORK_DIR={work_dir}

        if ! command -v docker >/dev/null 2>&1; then
            echo "docker executable not found" >&2
            exit 127
        fi

        declare -a volumes=()

        _add_volume() {{
            local dir="$1"
            local mode="${{2:-}}"
            if [[ -z "$dir" || ! -d "$dir" ]]; then
                return
            fi
            local spec="${{dir}}:${{dir}}"
            if [[ -n "$mode" ]]; then
                spec="${{spec}}:${{mode}}"
            fi
            volumes+=("-v" "$spec")
        }}

        _add_volume "$CACHE_DIR" "ro"
        _add_volume "$STATE_DIR"
        _add_volume "$WORK_DIR"

        declare -a env_names=()
        if [[ -n "${{TENZIR_BENCH_FORWARD_ENV:-}}" ]]; then
            IFS=',' read -ra env_names <<< "${{TENZIR_BENCH_FORWARD_ENV}}"
        fi
        declare -a forward_envs=()
        for name in "${{env_names[@]}}"; do
            name="${{name//[[:space:]]/}}"
            [[ -z "$name" ]] && continue
            if [[ -n "${{!name-}}" ]]; then
                forward_envs+=("-e" "$name")
            fi
        done

        exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" "${{volumes[@]}}" "${{forward_envs[@]}}" "$IMAGE" "$@"
        """,
    )
