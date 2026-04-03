"""Node-backed example fixture using the target-agnostic runtime API."""

from __future__ import annotations

import json
import logging
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from tenzir_bench.fixtures import FixtureHandle, current_context, current_options, fixture

_LOG = logging.getLogger(__name__)
_ENDPOINT_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+:\d+$")


@dataclass(frozen=True)
class NodeCatalogLookupOptions:
    """Configuration for the ``node_catalog_lookup`` example fixture."""

    events: int = 10_000
    max_partition_size: int = 1
    schema: str = "suricata"
    query_hit_index: int = 5_000
    startup_timeout_seconds: float = 120.0
    shutdown_timeout_seconds: float = 20.0


def _suricata_dns_event(index: int) -> dict[str, object]:
    value = f"bench-{index:06}.example"
    return {
        "timestamp": "2026-01-01T00:00:00.000000Z",
        "flow_id": index + 1,
        "src_ip": "10.0.0.1",
        "src_port": 53_000 + (index % 1_000),
        "dest_ip": "10.0.0.53",
        "dest_port": 53,
        "proto": "UDP",
        "event_type": "dns",
        "dns": {
            "version": 2,
            "type": "query",
            "id": index + 1,
            "flags": "0120",
            "qr": False,
            "rd": True,
            "ra": False,
            "aa": False,
            "tc": False,
            "rrname": value,
            "rrtype": "A",
            "rcode": "NOERROR",
            "ttl": None,
            "tx_id": None,
            "grouped": None,
            "answers": None,
        },
    }


def _write_dataset(path: Path, events: int, query_hit_index: int) -> str:
    if events <= 0:
        raise ValueError("'node_catalog_lookup.events' must be positive")
    if query_hit_index < 0 or query_hit_index >= events:
        raise ValueError("'node_catalog_lookup.query_hit_index' must refer to a generated event")
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOG.info("Materializing %s synthetic Suricata DNS events at %s", f"{events:,}", path)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(events):
            handle.write(json.dumps(_suricata_dns_event(index), separators=(",", ":")))
            handle.write("\n")
    return f"bench-{query_hit_index:06}.example"


def _wait_for_endpoint(
    *,
    log_path: Path,
    process: subprocess.Popen[str],
    startup_timeout_seconds: float,
) -> str:
    deadline = time.monotonic() + startup_timeout_seconds
    endpoint: str | None = None
    offset = 0
    while time.monotonic() < deadline:
        with log_path.open(encoding="utf-8") as log_handle:
            log_handle.seek(offset)
            while True:
                line = log_handle.readline()
                if not line:
                    break
                offset = log_handle.tell()
                candidate = line.strip()
                if _ENDPOINT_RE.match(candidate):
                    endpoint = candidate
                    break
        if endpoint is not None or process.poll() is not None:
            break
        time.sleep(0.1)
    if endpoint is not None:
        return endpoint
    detail = log_path.read_text(encoding="utf-8").strip() or "no output"
    raise RuntimeError(
        f"node_catalog_lookup fixture failed to start tenzir-node and emit an endpoint: {detail}",
    )


def _start_node(
    *,
    state_dir: Path,
    log_path: Path,
    max_partition_size: int,
    startup_timeout_seconds: float,
) -> tuple[subprocess.Popen[str], TextIO, str]:
    context = current_context()
    if context is None:
        raise RuntimeError("node_catalog_lookup fixture requires an active benchmark context")
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = context.runtime.popen_tenzir_node(
            args=[
                "-d",
                str(state_dir),
                "--endpoint=127.0.0.1:0",
                "--print-endpoint",
                "--max-partition-size",
                str(max_partition_size),
            ],
            env={"TENZIR_CONSOLE_FORMAT": "none"},
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        endpoint = _wait_for_endpoint(
            log_path=log_path,
            process=process,
            startup_timeout_seconds=startup_timeout_seconds,
        )
    except Exception:
        log_handle.close()
        raise
    _LOG.info("Started tenzir-node for catalog lookup benchmark at %s", endpoint)
    return process, log_handle, endpoint


def _seed_node(
    *,
    endpoint: str,
    state_dir: Path,
    dataset_path: Path,
    schema: str,
    pipeline_path: Path,
) -> None:
    context = current_context()
    if context is None:
        raise RuntimeError("node_catalog_lookup fixture requires an active benchmark context")
    pipeline_path.write_text(
        "\n".join(
            [
                f'from_file "{dataset_path}" {{',
                f"  read_{schema}",
                "}",
                "import",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = context.runtime.run_tenzir(
        args=[
            "-e",
            endpoint,
            "-d",
            str(state_dir),
            "-f",
            str(pipeline_path),
        ],
        env={"TENZIR_CONSOLE_FORMAT": "none"},
        capture_output=True,
        check=False,
        cwd=dataset_path.parent,
    )
    if result.returncode == 0:
        _LOG.info("Seeded node at %s from %s", endpoint, dataset_path)
        return
    detail = (result.stderr or result.stdout or "").strip() or "no output"
    raise RuntimeError(f"failed to seed catalog lookup node: {detail}")


def _stop_node(
    process: subprocess.Popen[str],
    *,
    shutdown_timeout_seconds: float,
) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=shutdown_timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


@fixture(name="node_catalog_lookup", replace=True, options=NodeCatalogLookupOptions)
def node_catalog_lookup() -> FixtureHandle:
    """Start a node, seed it from a synthetic dataset, and expose the lookup key."""

    context = current_context()
    if context is None:
        raise RuntimeError("node_catalog_lookup fixture requires an active benchmark context")
    options = current_options("node_catalog_lookup")
    if not isinstance(options, NodeCatalogLookupOptions):
        raise ValueError("invalid options for fixture 'node_catalog_lookup'")

    benchmark_root = context.output_root / "node-catalog-lookup"
    dataset_path = context.dataset_path
    state_dir = benchmark_root / "state"
    import_pipeline_path = benchmark_root / "seed.tql"
    log_path = benchmark_root / "logs" / "node.log"
    query_value = _write_dataset(dataset_path, options.events, options.query_hit_index)
    process, log_handle, endpoint = _start_node(
        state_dir=state_dir,
        log_path=log_path,
        max_partition_size=options.max_partition_size,
        startup_timeout_seconds=options.startup_timeout_seconds,
    )
    try:
        _seed_node(
            endpoint=endpoint,
            state_dir=state_dir,
            dataset_path=dataset_path,
            schema=options.schema,
            pipeline_path=import_pipeline_path,
        )
    except Exception:
        _stop_node(process, shutdown_timeout_seconds=options.shutdown_timeout_seconds)
        log_handle.close()
        raise
    return FixtureHandle(
        env={
            "TENZIR_ENDPOINT": endpoint,
            "BENCHMARK_LOOKUP_VALUE": query_value,
        },
        teardown=lambda: _teardown_node_fixture(
            process=process,
            log_handle=log_handle,
            shutdown_timeout_seconds=options.shutdown_timeout_seconds,
        ),
    )


def _teardown_node_fixture(
    *,
    process: subprocess.Popen[str],
    log_handle: TextIO,
    shutdown_timeout_seconds: float,
) -> None:
    try:
        _stop_node(process, shutdown_timeout_seconds=shutdown_timeout_seconds)
    finally:
        log_handle.close()
