"""Command line interface for the Tenzir benchmarking harness."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

import click

from .compare import resolve_binaries, run_compare
from .datasets import DatasetManager
from .definitions import BenchmarkDefinition
from .evaluation import evaluate as evaluate_results
from .executor import BenchmarkExecutor
from .paths import BenchPaths
from .publisher import Publisher
from .reports import Report, load_report, select_fastest
from .runners import RunnerRegistry
from .specs import discover_definitions
from .syncer import sync as sync_results

_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING,
               "error": logging.ERROR, "critical": logging.CRITICAL}


def _configure_logging(level: str) -> None:
    numeric = _LOG_LEVELS.get(level.lower())
    if numeric is None:
        raise click.BadParameter(f"Unknown log level '{level}'.")
    logging.basicConfig(level=numeric, format="[%(levelname)s] %(message)s")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--log-level", default="info", show_default=True,
              type=click.Choice(sorted(_LOG_LEVELS)), help="Set log verbosity.")
@click.pass_context
def main(ctx: click.Context, log_level: str) -> None:
    """Tenzir benchmark harness."""
    _configure_logging(log_level)
    ctx.obj = BenchPaths.create()


@main.command()
@click.option("--force", is_flag=True, help="Re-download and regenerate datasets.")
@click.pass_obj
def prepare(paths: BenchPaths, force: bool) -> None:
    """Download and prepare benchmark datasets."""
    manager = DatasetManager(paths)
    manager.prepare(force=force)


@main.command(context_settings={"ignore_unknown_options": True})
@click.option("--filter", "pattern", help="Run only benchmarks matching the glob pattern.")
@click.option(
    "--benchmark",
    "benchmarks",
    multiple=True,
    help="Benchmark id, glob, file, or directory to run. Repeat to select multiple.",
)
@click.option("--tenzir-bin", type=click.Path(path_type=Path), help="Path to the Tenzir binary.")
@click.option("--validate", is_flag=True, help="Validate benchmark commands without executing them.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print resolved benchmark invocations without validating them.",
)
@click.option("--verbose", is_flag=True, help="Print each benchmark invocation once in copyable form.")
@click.argument("tenzir_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_obj
def run(
    paths: BenchPaths,
    pattern: str | None,
    benchmarks: tuple[str, ...],
    tenzir_bin: Path | None,
    validate: bool,
    dry_run: bool,
    verbose: bool,
    tenzir_args: Sequence[str],
) -> None:
    """Execute benchmarks and record reports."""
    try:
        _validate_mode_flags(validate, dry_run)
        if pattern and benchmarks:
            raise click.BadParameter("--filter and --benchmark are mutually exclusive")
        tenzir = tenzir_bin or _resolve_tenzir()
        registry = RunnerRegistry()
        executor = BenchmarkExecutor(
            paths,
            tenzir,
            registry,
            tenzir_args=tenzir_args,
            validate=validate,
            dry_run=dry_run,
            verbose=verbose,
        )
        root = _detect_repo_root(tenzir)
        contexts = _load_contexts(
            executor,
            root=root,
            pattern=pattern,
            benchmarks=benchmarks,
        )
        if not contexts:
            build_version = executor.build_info().version or "unknown"
            click.echo(
                "No runnable benchmarks matched the selection "
                f"for Tenzir build {build_version}. Check benchmark selectors "
                "and min_version/max_version constraints.",
            )
            return
        generated_reports: list[Path] = []
        executor.prepare_progress(contexts)
        for context in contexts:
            generated_reports.extend(executor.execute(context))
        if generated_reports and not validate and not dry_run:
            _print_run_summary(generated_reports)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command()
@click.option("--full", is_flag=True, help="Fetch all remote results regardless of architecture.")
@click.option("--refresh", is_flag=True, help="Ignore cached metadata TTL when syncing.")
@click.pass_obj
def sync(paths: BenchPaths, full: bool, refresh: bool) -> None:
    logging.info("Synchronising metadata and results")
    sync_results(paths, full=full, refresh=refresh)


@main.command()
@click.option("--runs", type=click.Path(path_type=Path),
              help="Directory containing benchmark run reports.")
@click.option("--base", type=click.Path(path_type=Path), help="Baseline directory for evaluation.")
@click.option("--compact", is_flag=True, help="Render a compact summary table.")
@click.pass_obj
def eval(paths: BenchPaths, runs: Path | None, base: Path | None, compact: bool) -> None:
    """Evaluate benchmark results against references."""
    runs_dir = runs or paths.results_state_dir
    evaluate_results(paths, runs_dir, base, compact)


@main.command()
@click.option("--runs", type=click.Path(path_type=Path),
              help="Directory with run reports to publish.")
@click.option("--destination", required=True, help="Remote bucket/prefix to publish to.")
@click.option("--force", is_flag=True, help="Re-upload artifacts even if they exist remotely.")
@click.pass_obj
def publish(paths: BenchPaths, runs: Path | None, destination: str, force: bool) -> None:
    """Publish run reports to the remote store."""
    runs_dir = runs or paths.results_state_dir
    publisher = Publisher()
    publisher.publish(runs_dir, destination, force=force)


@main.command(context_settings={"ignore_unknown_options": True})
@click.option("--compact", is_flag=True, help="Render a compact summary table.")
@click.option("--validate", is_flag=True, help="Validate benchmark commands without executing them.")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print resolved benchmark invocations without validating them.",
)
@click.option("--verbose", is_flag=True, help="Print each benchmark invocation once in copyable form.")
@click.argument("arguments", nargs=-1, metavar="PATH")
@click.pass_obj
def compare(
    bench_paths: BenchPaths,
    compact: bool,
    validate: bool,
    dry_run: bool,
    verbose: bool,
    arguments: Sequence[str],
) -> None:
    """Compare multiple Tenzir builds by running benchmarks in PATH.

    \b
    The following markers can be interspersed with PATH values:
      --base PATH        baseline build (required)
      --candidate PATH   candidate build (repeatable)
      --run              force executing the next --base/--candidate path
      --OPTION           Tenzir option for the most recent build path

    PATH entries may refer to directories/files that contain a Tenzir binary
    or docker images using the docker://IMAGE notation. Options that take
    values must use the `--option=value` form.
    """
    try:
        _validate_mode_flags(validate, dry_run)
        binaries, benchmark_dirs = _parse_compare_arguments(arguments)
        resolved = resolve_binaries(bench_paths, binaries)
        run_compare(
            bench_paths,
            resolved,
            compact,
            benchmark_dirs,
            validate=validate,
            dry_run=dry_run,
            verbose=verbose,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    main()


def _resolve_tenzir() -> Path:
    resolved = shutil.which("tenzir")
    if not resolved:
        raise click.ClickException("Unable to locate 'tenzir' executable; specify --tenzir-bin")
    return Path(resolved)


def _validate_mode_flags(validate: bool, dry_run: bool) -> None:
    if validate and dry_run:
        raise click.BadParameter("--validate and --dry-run are mutually exclusive")


def _detect_repo_root(tenzir: Path) -> Path:
    for start in (Path.cwd().resolve(), tenzir.resolve()):
        if root := _find_repo_root(start):
            return root
    raise click.ClickException(
        "Unable to locate a repository root containing bench/. "
        "Run the command from a tenzir checkout or point --tenzir-bin at a binary inside one.",
    )


def _find_repo_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for candidate in (current, *current.parents):
        if (candidate / "bench").is_dir():
            return candidate
    return None


def _load_contexts(
    executor: BenchmarkExecutor,
    *,
    root: Path,
    pattern: str | None,
    benchmarks: Sequence[str],
) -> list:
    definitions: list[BenchmarkDefinition] = []
    if benchmarks:
        seen: set[tuple[Path, str]] = set()
        for selector in benchmarks:
            for definition in _discover_selected_definitions(executor, root=root, selector=selector):
                key = (definition.path, definition.id)
                if key in seen:
                    continue
                seen.add(key)
                definitions.append(definition)
    else:
        definitions = list(
            discover_definitions(
                pattern,
                version_supplier=lambda: executor.build_info().version,
                root=root,
            ),
        )
    return [context for definition in definitions if (context := executor.create_context(definition)) is not None]


def _discover_selected_definitions(
    executor: BenchmarkExecutor,
    *,
    root: Path,
    selector: str,
) -> list[BenchmarkDefinition]:
    candidates = [
        Path(selector),
        root / selector,
        root / "bench" / selector,
        root / "bench" / "benchmarks" / selector,
    ]
    for candidate in candidates:
        if candidate.exists():
            return discover_definitions(
                str(candidate.resolve()),
                version_supplier=lambda: executor.build_info().version,
                root=root,
            )
    return discover_definitions(
        selector,
        version_supplier=lambda: executor.build_info().version,
        root=root,
    )


def _print_run_summary(report_paths: Sequence[Path]) -> None:
    reports = _select_fastest_from_paths(report_paths)
    if not reports:
        return
    click.echo()
    click.echo(_render_run_summary(reports))


def _select_fastest_from_paths(report_paths: Sequence[Path]) -> list[Report]:
    grouped: dict[str, list[Report]] = {}
    for path in report_paths:
        report = load_report(path)
        if report is None:
            continue
        grouped.setdefault(report.pipeline, []).append(report)
    fastest = select_fastest(grouped)
    return [fastest[pipeline] for pipeline in sorted(fastest)]


def _render_run_summary(reports: Sequence[Report]) -> str:
    benchmark_width = max(len("benchmark"), *(len(report.pipeline) for report in reports))
    lines = [
        f"{'benchmark':<{benchmark_width}}  {'runtime':>10}  {'peak rss':>10}",
        f"{'-' * benchmark_width}  {'-' * 10}  {'-' * 10}",
    ]
    for report in reports:
        lines.append(
            f"{report.pipeline:<{benchmark_width}}  "
            f"{report.wall_clock:>10.2f}s  "
            f"{report.rss_kb / 1024:>9.0f} MB",
        )
    return "\n".join(lines)


def _parse_compare_arguments(
    arguments: Sequence[str],
) -> tuple[list[tuple[str, bool, tuple[str, ...]]], list[Path]]:
    binaries: list[tuple[str, bool, tuple[str, ...]]] = []
    benchmark_dirs: list[Path] = []
    force_next = False
    current_index: int | None = None
    i = 0
    while i < len(arguments):
        token = arguments[i]
        if token == "--run":
            force_next = True
            current_index = None
        elif token in {"--base", "--candidate"}:
            i += 1
            if i >= len(arguments):
                raise click.BadParameter(f"{token} requires a path")
            entry = (arguments[i], force_next, ())
            if token == "--base":
                if binaries:
                    raise click.BadParameter("only one --base may be specified")
                binaries.append(entry)
                current_index = 0
            else:
                binaries.append(entry)
                current_index = len(binaries) - 1
            force_next = False
        elif token.startswith("-"):
            if current_index is None:
                raise click.BadParameter(
                    f"{token} must follow a --base or --candidate path; use --option=value for options with values",
                )
            path, force, tenzir_args = binaries[current_index]
            binaries[current_index] = (path, force, (*tenzir_args, token))
        else:
            benchmark_dirs.append(Path(token))
            current_index = None
        i += 1
    if not binaries:
        raise click.BadParameter("at least one --base must be specified")
    if len(binaries) < 2:
        raise click.BadParameter("at least one --candidate must be specified")
    return binaries, benchmark_dirs
