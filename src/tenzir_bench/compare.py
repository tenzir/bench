"""Helpers for comparing builds."""

from __future__ import annotations

import hashlib
import logging
import re
import shlex
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from typing import TypeAlias

from .definitions import BenchmarkDefinition, BenchmarkError
from .executor import BenchmarkContext, BenchmarkExecutor, BuildInfo, build_result_id
from .hardware import current_hardware_key
from .paths import BenchPaths
from .publisher import Publisher
from .references import (
    ReportIdentity,
    download_reference_reports,
    missing_report_identities,
    report_identity,
)
from .reports import Report, load_reports, report_requires_refresh, select_fastest
from .runners import RunnerRegistry
from .specs import load_definitions_from_paths

_LOG = logging.getLogger(__name__)
LoadedDefinition: TypeAlias = BenchmarkDefinition
PipelineReports: TypeAlias = dict[str, Report]


@dataclass(frozen=True)
class CompareBuild:
    label: str
    binary: Path | None
    force: bool = False
    tenzir_args: tuple[str, ...] = ()
    reference_destination: str | None = None
    target: str | None = None
    version: str | None = None


def resolve_binaries(
    paths: BenchPaths,
    entries: Sequence[tuple[str, bool, tuple[str, ...]]],
) -> list[CompareBuild]:
    resolved: list[CompareBuild] = []
    for value, force, tenzir_args in entries:
        binary = _resolve_entry(paths, value)
        resolved.append(
            CompareBuild(
                label=str(value),
                binary=binary,
                force=force,
                tenzir_args=tenzir_args,
            ),
        )
    if len(resolved) < 2:
        raise ValueError("compare requires at least two binaries")
    return resolved


def resolve_entry(paths: BenchPaths, value: str) -> Path:
    return _resolve_entry(paths, value)


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
    return candidate


def run_compare(
    paths: BenchPaths,
    binaries: list[CompareBuild],
    compact: bool,
    benchmark_dirs: Sequence[Path],
    *,
    validate: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    build_reports = prepare_compare_build_reports(
        paths,
        binaries,
        benchmark_dirs,
        validate=validate,
        dry_run=dry_run,
        verbose=verbose,
    )
    if validate or dry_run:
        return
    baseline_label, baseline_reports = build_reports[0]
    candidate_reports = build_reports[1:]
    if compact:
        _render_compact_table(baseline_label, baseline_reports, candidate_reports)
    else:
        _render_detailed(baseline_label, baseline_reports, candidate_reports)


def prepare_compare_build_reports(
    paths: BenchPaths,
    builds: Sequence[CompareBuild],
    benchmark_dirs: Sequence[Path],
    *,
    validate: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> list[tuple[str, PipelineReports]]:
    if not builds:
        return []
    compare_root = paths.results_state_dir / "compare"
    if not validate and not dry_run:
        shutil.rmtree(compare_root, ignore_errors=True)
    compare_root.mkdir(parents=True, exist_ok=True)
    registry = RunnerRegistry()
    prepared: list[tuple[str, PipelineReports]] = []
    for build in builds:
        reports = prepare_compare_reports_for_build(
            paths,
            build,
            benchmark_dirs,
            compare_root=compare_root,
            registry=registry,
            validate=validate,
            dry_run=dry_run,
            verbose=verbose,
        )
        prepared.append((build.label, reports))
    return prepared


def prepare_compare_reports_for_build(
    paths: BenchPaths,
    build: CompareBuild,
    benchmark_dirs: Sequence[Path],
    *,
    compare_root: Path | None = None,
    registry: RunnerRegistry | None = None,
    validate: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, Report]:
    active_registry = registry or RunnerRegistry()
    if build.reference_destination:
        return _prepare_reference_backed_reports(
            paths,
            build,
            benchmark_dirs,
            compare_root=compare_root,
            registry=active_registry,
            validate=validate,
            dry_run=dry_run,
            verbose=verbose,
        )
    return _prepare_local_reports(
        paths,
        build,
        benchmark_dirs,
        compare_root=compare_root,
        registry=active_registry,
        validate=validate,
        dry_run=dry_run,
        verbose=verbose,
    )


def expected_report_identities(
    paths: BenchPaths,
    build: CompareBuild,
    benchmark_dirs: Sequence[Path],
) -> set[ReportIdentity]:
    version = build.version
    if version is None and build.binary is not None:
        version = (
            _build_executor(
                paths,
                build,
                RunnerRegistry(),
                validate=False,
                dry_run=False,
                verbose=False,
            )
            .build_info()
            .version
        )
    if version is not None and benchmark_dirs:
        definitions = _definitions_for_paths(benchmark_dirs, version=version)
        return {_definition_identity(definition) for definition in definitions}
    if build.binary is None:
        raise RuntimeError(
            f"{build.label}: expected a local build path or semantic version to resolve benchmark identities",
        )
    executor = _build_executor(
        paths,
        build,
        RunnerRegistry(),
        validate=False,
        dry_run=False,
        verbose=False,
    )
    contexts = list(_discover_from_dirs(executor, benchmark_dirs))
    return {_definition_identity(context.definition) for context in contexts}


def _prepare_reference_backed_reports(
    paths: BenchPaths,
    build: CompareBuild,
    benchmark_dirs: Sequence[Path],
    *,
    compare_root: Path | None,
    registry: RunnerRegistry,
    validate: bool,
    dry_run: bool,
    verbose: bool,
) -> dict[str, Report]:
    target = build.target or (build.binary and _compare_target(build.binary))
    if not target:
        raise RuntimeError(f"{build.label}: reference-backed build requires a target")
    if build.reference_destination is None:
        raise RuntimeError(f"{build.label}: reference-backed build requires a destination")
    expected = expected_report_identities(paths, build, benchmark_dirs)
    remote_reports = download_reference_reports(
        build.reference_destination,
        benchmarks=sorted({benchmark_id for benchmark_id, _ in expected}),
        hardware_key=current_hardware_key(),
        target=target,
    )
    stale_identities = {
        identity for identity, report in remote_reports.items() if report_requires_refresh(report)
    }
    remote_reports = {
        identity: report
        for identity, report in remote_reports.items()
        if identity in expected and identity not in stale_identities
    }
    missing = missing_report_identities(expected, remote_reports)
    if not missing:
        return _reports_by_pipeline(remote_reports.values())
    if build.binary is None:
        raise RuntimeError(
            f"{build.label}: reference is missing benchmark reports and no local build path is available to backfill",
        )
    local_reports = _prepare_local_reports(
        paths,
        build,
        benchmark_dirs,
        compare_root=compare_root,
        registry=registry,
        validate=validate,
        dry_run=dry_run,
        verbose=verbose,
    )
    if validate or dry_run:
        return local_reports
    reports_to_publish = {
        pipeline: report
        for pipeline, report in local_reports.items()
        if report_identity(report) in missing
    }
    if reports_to_publish:
        Publisher().publish_reports(
            reports_to_publish,
            build.reference_destination,
            force=bool(stale_identities),
        )
    combined = dict(_reports_by_pipeline(remote_reports.values()))
    combined.update(reports_to_publish)
    return combined


def _prepare_local_reports(
    paths: BenchPaths,
    build: CompareBuild,
    benchmark_dirs: Sequence[Path],
    *,
    compare_root: Path | None,
    registry: RunnerRegistry,
    validate: bool,
    dry_run: bool,
    verbose: bool,
) -> dict[str, Report]:
    if build.binary is None:
        raise RuntimeError(f"{build.label}: local compare build is missing a binary path")
    executor = _build_executor(
        paths,
        build,
        registry,
        validate=validate,
        dry_run=dry_run,
        verbose=verbose,
    )
    contexts = list(_discover_from_dirs(executor, benchmark_dirs))
    if not contexts:
        _LOG.error("No benchmarks found to execute")
        return {}
    if dry_run:
        for context in contexts:
            executor.validate(context)
        return {}
    compare_dir = _compare_dir(compare_root, build, executor)
    if validate:
        for context in contexts:
            executor.validate(context)
        return {}
    _ = executor.ensure_reports(contexts, compare_dir, force=build.force)
    return select_fastest(load_reports(compare_dir))


def _build_executor(
    paths: BenchPaths,
    build: CompareBuild,
    registry: RunnerRegistry,
    *,
    validate: bool,
    dry_run: bool,
    verbose: bool,
) -> BenchmarkExecutor:
    if build.binary is None:
        raise RuntimeError(f"{build.label}: local executor requires a binary path")
    return BenchmarkExecutor(
        paths,
        build.binary,
        registry,
        tenzir_args=build.tenzir_args,
        target=build.target,
        validate=validate,
        dry_run=dry_run,
        verbose=verbose,
    )


def _compare_dir(
    compare_root: Path | None, build: CompareBuild, executor: BenchmarkExecutor
) -> Path:
    root = compare_root or executor.paths.results_state_dir / "compare"
    info = executor.build_info()
    label = _display_label(info, build.binary or Path(build.label), build.tenzir_args)
    return root / _cache_key(
        info, build.binary or Path(build.label), build.tenzir_args, label_override=label
    )


def _reports_by_pipeline(reports: Iterable[Report]) -> dict[str, Report]:
    return {report.pipeline: report for report in reports}


def _compare_target(binary: Path) -> str:
    if binary.suffix == ".sh" and binary.parent.name == "docker":
        return "docker"
    return "static"


def _definitions_for_paths(paths: Sequence[Path], *, version: str) -> list[LoadedDefinition]:
    return load_definitions_from_paths(
        list(paths),
        version_supplier=lambda: version,
    )


def _definition_identity(definition: LoadedDefinition) -> ReportIdentity:
    benchmark_id = getattr(definition, "benchmark_id", None)
    implementation_id = getattr(definition, "implementation_id", None)
    path = getattr(definition, "path", None)
    if not benchmark_id:
        raise RuntimeError(f"{path}: missing benchmark_id in benchmark definition")
    if not implementation_id:
        raise RuntimeError(f"{path}: missing implementation_id in benchmark definition")
    return benchmark_id, implementation_id


def _discover_from_dirs(
    executor: BenchmarkExecutor, dirs: Sequence[Path]
) -> Iterable[BenchmarkContext]:
    if not dirs:
        yield from executor.discover(pattern=None)
        return
    try:
        definitions = load_definitions_from_paths(
            list(dirs),
            version_supplier=lambda: executor.build_info().version,
        )
    except (BenchmarkError, FileNotFoundError, RuntimeError) as exc:
        _LOG.error("%s", exc)
        return
    for definition in definitions:
        context = executor.create_context(definition)
        if context:
            yield context


def _render_compact_table(
    baseline_label: str,
    baseline_reports: dict[str, Report],
    candidate_reports: Sequence[tuple[str, dict[str, Report]]],
) -> None:
    candidate_label_lengths = [len(label) for label, _ in candidate_reports]
    label_width = max([len("build"), len(baseline_label), *candidate_label_lengths])
    header = f"{'build':<{label_width}} {'seconds':>10} {'Δseconds':>12} {'rss':>10} {'Δrss':>12}"
    separator = "-" * len(header)
    pipeline_set: set[str] = set(baseline_reports)
    for _, reports in candidate_reports:
        pipeline_set.update(reports.keys())
    pipelines = sorted(pipeline_set)
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


def _render_detailed(
    baseline_label: str,
    baseline_reports: dict[str, Report],
    candidate_reports: Sequence[tuple[str, dict[str, Report]]],
) -> None:
    pipeline_set: set[str] = set(baseline_reports)
    for _, reports in candidate_reports:
        pipeline_set.update(reports.keys())
    pipelines = sorted(pipeline_set)
    for pipeline in pipelines:
        print(f"Pipeline: {pipeline}")
        base = baseline_reports.get(pipeline)
        print(f"  {baseline_label}: {_detail_row(base, None)}")
        for label, reports in candidate_reports:
            cand = reports.get(pipeline)
            print(f"  {label}: {_detail_row(cand, base)}")
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
            _rss_value(baseline),
            _rss_value(report),
        )
    else:
        seconds_delta = ""
        rss_delta = ""
    return f"{label:<{label_width}} {seconds:>10} {seconds_delta:>12} {rss:>10} {rss_delta:>12}"


def _fmt_seconds(report: Report | None) -> str:
    return f"{report.wall_clock:.2f}" if report else "-"


def _fmt_rss(report: Report | None) -> str:
    value = _rss_value(report)
    if value is None:
        return "-"
    return f"{value / 1024:.0f} MB"


def _fmt_percent_change(base: float | None, cand: float | None) -> str:
    if base is None or cand is None or base == 0:
        return "-"
    delta = ((cand - base) / base) * 100
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}%"


def _detail_row(report: Report | None, baseline: Report | None) -> str:
    if report is None:
        return "missing"
    rss = _fmt_rss(report)
    row = f"wall={report.wall_clock:.2f}s rss={rss} source={report.path}"
    if baseline is None:
        return row
    return f"{row} Δwall={_fmt_detail_seconds(baseline, report)} Δrss={_fmt_detail_rss(baseline, report)}"


def _fmt_detail_seconds(base: Report, cand: Report) -> str:
    delta = cand.wall_clock - base.wall_clock
    sign = "+" if delta > 0 else ""
    pct = _fmt_percent_change(base.wall_clock, cand.wall_clock)
    return f"{sign}{delta:.2f}s ({pct})"


def _fmt_detail_rss(base: Report, cand: Report) -> str:
    base_rss = _rss_value(base)
    cand_rss = _rss_value(cand)
    if base_rss is None or cand_rss is None:
        return "-"
    delta = int(cand_rss - base_rss)
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta}k"


def _rss_value(report: Report | None) -> int | None:
    if report is None:
        return None
    return report.rss_kb


def _label(path: Path) -> str:
    if path.name == "tenzir" and path.parent.name == "bin":
        return path.parent.parent.name
    return path.parent.name if path.is_file() else path.name


def _display_label(info: BuildInfo, path: Path, tenzir_args: Sequence[str]) -> str:
    label = info.build_id if info.build_id != "unknown" else _label(path)
    if not tenzir_args:
        return label
    return f"{label} {' '.join(tenzir_args)}"


def unique_labels(labels: Sequence[str], binaries: Sequence[Path]) -> list[str]:
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    unique: list[str] = []
    for label, binary in zip(labels, binaries, strict=True):
        if counts[label] == 1:
            unique.append(label)
            continue
        digest = hashlib.sha256(str(binary).encode("utf-8")).hexdigest()[:8]
        unique.append(f"{label}[{digest}]")
    return unique


_unique_labels = unique_labels


def _cache_key(
    info: BuildInfo,
    path: Path,
    tenzir_args: Sequence[str],
    *,
    label_override: str | None = None,
) -> str:
    safe_label = re.sub(
        r"[^A-Za-z0-9._-]",
        "_",
        label_override or _display_label(info, path, tenzir_args),
    )
    digest = hashlib.sha256(
        f"{info.path}\0{build_result_id(info, tenzir_args)}\0{info.build_type}".encode("utf-8"),
    ).hexdigest()[:12]
    return f"{safe_label}-{digest}"


def _ensure_docker_wrapper(paths: BenchPaths, image: str) -> Path:
    wrapper_dir = paths.ensure_dir(paths.state_dir / "docker")
    digest = hashlib.sha256(image.encode("utf-8")).hexdigest()[:8]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", image)
    wrapper_path = wrapper_dir / f"{safe}-{digest}.sh"
    script = _docker_wrapper_script(image, paths)
    _ = wrapper_path.write_text(script, encoding="utf-8")
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

        if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
            docker pull "$IMAGE" >&2
        fi

        ENTRYPOINT_JSON="$(docker image inspect --format '{{{{json .Config.Entrypoint}}}}' "$IMAGE")"
        CMD_JSON="$(docker image inspect --format '{{{{json .Config.Cmd}}}}' "$IMAGE")"

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

        PYTHON_WRAPPER="$(cat <<'PY'
        import json
        import os
        import subprocess
        import sys

        entrypoint = json.loads(sys.argv[1]) or []
        default_cmd = json.loads(sys.argv[2]) or []
        sep = sys.argv.index("--")
        runtime_args = sys.argv[sep + 1 :]
        argv = entrypoint + (runtime_args if runtime_args else default_cmd)
        if not argv:
            print("docker image is missing an entrypoint/cmd for tenzir-bench", file=sys.stderr)
            raise SystemExit(127)
        proc = subprocess.Popen(argv)
        _pid, status, rusage = os.wait4(proc.pid, 0)
        exit_code = os.waitstatus_to_exitcode(status)
        if exit_code == 0:
            print(f"tenzir-bench-maxrss={{rusage.ru_maxrss}}", file=sys.stderr)
        raise SystemExit(exit_code)
        PY
        )"

        exec docker run --rm --network=host --user "$(id -u)":"$(id -g)" "${{volumes[@]}}" "${{forward_envs[@]}}" --entrypoint python3 "$IMAGE" -c "$PYTHON_WRAPPER" "$ENTRYPOINT_JSON" "$CMD_JSON" -- "$@"
        """,
    )
