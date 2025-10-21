"""Command line interface for the Tenzir benchmarking harness."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

import click

from .datasets import DatasetManager
from .executor import BenchmarkExecutor
from .runners import RunnerRegistry
from .paths import BenchPaths

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
    """Synchronise cached results and metadata from the central store."""
    logging.info("Sync is not implemented yet.")
    logging.debug("full=%s refresh=%s results_cache=%s", full, refresh, paths.results_cache_dir)


@main.command()
@click.option("--runs", type=click.Path(path_type=Path),
              help="Directory containing benchmark run reports.")
@click.option("--base", type=click.Path(path_type=Path), help="Baseline directory for evaluation.")
@click.option("--compact", is_flag=True, help="Render a compact summary table.")
@click.pass_obj
def eval(paths: BenchPaths, runs: Optional[Path], base: Optional[Path], compact: bool) -> None:
    """Evaluate benchmark results against references."""
    runs_dir = runs or paths.results_state_dir
    logging.info("Evaluation is not implemented yet.")
    logging.debug(
        "runs=%s base=%s compact=%s state_dir=%s", runs_dir, base, compact, paths.results_state_dir
    )


@main.command()
@click.option("--runs", type=click.Path(path_type=Path),
              help="Directory with run reports to publish.")
@click.option("--destination", required=True, help="Remote bucket/prefix to publish to.")
@click.option("--force", is_flag=True, help="Re-upload artifacts even if they exist remotely.")
@click.pass_obj
def publish(paths: BenchPaths, runs: Optional[Path], destination: str, force: bool) -> None:
    """Publish run reports to the remote store."""
    runs_dir = runs or paths.results_state_dir
    logging.info("Publish is not implemented yet.")
    logging.debug(
        "runs=%s destination=%s force=%s results_state_dir=%s",
        runs_dir,
        destination,
        force,
        paths.results_state_dir,
    )


@main.command()
@click.argument("baseline", type=click.Path(path_type=Path))
@click.argument("candidate", type=click.Path(path_type=Path))
@click.option("--compact", is_flag=True, help="Render a compact summary table.")
@click.pass_obj
def compare(paths: BenchPaths, baseline: Path, candidate: Path, compact: bool) -> None:
    """Run benchmarks for two Tenzir builds and compare results."""
    logging.info("Compare is not implemented yet.")
    logging.debug(
        "baseline=%s candidate=%s compact=%s results_dir=%s",
        baseline,
        candidate,
        compact,
        paths.results_state_dir,
    )


if __name__ == "__main__":  # pragma: no cover
    main()


def _resolve_tenzir() -> Path:
    resolved = shutil.which("tenzir")
    if not resolved:
        raise click.ClickException("Unable to locate 'tenzir' executable; specify --tenzir-bin")
    return Path(resolved).resolve()
