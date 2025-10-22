"""Command line interface for the Tenzir benchmarking harness."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

import click

from .datasets import DatasetManager
from .evaluation import evaluate as evaluate_results
from .executor import BenchmarkExecutor
from .paths import BenchPaths
from .publisher import Publisher
from .runners import RunnerRegistry
from .syncer import sync as sync_results
from .compare import resolve_binaries, run_compare

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


@main.command()
@click.option("--filter", "pattern", help="Run only benchmarks matching the glob pattern.")
@click.option("--tenzir-bin", type=click.Path(path_type=Path), help="Path to the Tenzir binary.")
@click.pass_obj
def run(paths: BenchPaths, pattern: Optional[str], tenzir_bin: Optional[Path]) -> None:
    """Execute benchmarks and record reports."""
    tenzir = tenzir_bin or _resolve_tenzir()
    registry = RunnerRegistry()
    executor = BenchmarkExecutor(paths, tenzir, registry)
    for context in executor.discover(pattern):
        executor.execute(context)


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
def eval(paths: BenchPaths, runs: Optional[Path], base: Optional[Path], compact: bool) -> None:
    """Evaluate benchmark results against references."""
    runs_dir = runs or paths.results_state_dir
    evaluate_results(paths, runs_dir, base, compact)


@main.command()
@click.option("--runs", type=click.Path(path_type=Path),
              help="Directory with run reports to publish.")
@click.option("--destination", required=True, help="Remote bucket/prefix to publish to.")
@click.option("--force", is_flag=True, help="Re-upload artifacts even if they exist remotely.")
@click.pass_obj
def publish(paths: BenchPaths, runs: Optional[Path], destination: str, force: bool) -> None:
    """Publish run reports to the remote store."""
    runs_dir = runs or paths.results_state_dir
    publisher = Publisher()
    publisher.publish(runs_dir, destination, force=force)


@main.command()
@click.argument("binaries", nargs=-1, type=str)
@click.option("--filter", "pattern", help="Run only benchmarks matching the glob pattern.")
@click.option("--compact", is_flag=True, help="Render a compact summary table.")
@click.pass_obj
def compare(paths: BenchPaths, binaries: Sequence[str], compact: bool, pattern: Optional[str]) -> None:
    """Compare multiple Tenzir builds (baseline first)."""
    resolved = resolve_binaries(binaries)
    run_compare(paths, resolved, compact, pattern)


if __name__ == "__main__":  # pragma: no cover
    main()


def _resolve_tenzir() -> Path:
    resolved = shutil.which("tenzir")
    if not resolved:
        raise click.ClickException("Unable to locate 'tenzir' executable; specify --tenzir-bin")
    return Path(resolved).resolve()
