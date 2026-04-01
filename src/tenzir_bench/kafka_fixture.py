"""Kafka benchmark fixture backed by Docker Compose."""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .fixtures import FixtureHandle, FixtureUnavailable, current_context, current_options, fixture

_LOG = logging.getLogger(__name__)
_FIXTURE_BROKERS = "127.0.0.1:9092"
_DATASET_REPETITIONS = 500


@dataclass(frozen=True)
class KafkaFixtureOptions:
    """Structured configuration for the ``kafka`` benchmark fixture."""

    compose_file: str = "compose.yaml"
    service: str = "redpanda"
    topic: str = "tenzir-bench"
    bootstrap_servers: str = _FIXTURE_BROKERS
    partitions: int = 1
    replication_factor: int = 1
    wait_timeout_seconds: float = 120.0
    wait_poll_interval_seconds: float = 1.0


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _resolve_path(benchmark_path: Path, raw: str) -> Path:
    value = raw.strip()
    if not value:
        raise ValueError("'kafka.compose_file' must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (benchmark_path.parent / path).resolve()
    else:
        path = path.resolve()
    return path


def _project_name(benchmark_path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", benchmark_path.stem.lower()).strip("-") or "kafka"
    digest = hashlib.sha1(str(benchmark_path).encode("utf-8")).hexdigest()[:8]
    return f"tenzir-bench-{slug[:24]}-{digest}"


def _group_id(benchmark_path: Path, topic: str) -> str:
    digest = hashlib.sha1(f"{benchmark_path}\0{topic}".encode("utf-8")).hexdigest()[:12]
    return f"tenzir-bench-{digest}"


def _compose_base_args(*, compose_file: Path, project_name: str) -> list[str]:
    return ["docker", "compose", "-f", str(compose_file), "-p", project_name]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    description: str,
    stdin: TextIO | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        stdin=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if not check or result.returncode == 0:
        return result
    detail = (result.stderr or result.stdout or "").strip() or "no output"
    raise RuntimeError(f"{description} failed (exit code {result.returncode}): {detail}")


def _wait_for_cluster(
    base_args: list[str],
    *,
    cwd: Path,
    service: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_detail = "no output"
    while time.monotonic() < deadline:
        result = _run(
            [
                *base_args,
                "exec",
                "-T",
                service,
                "rpk",
                "topic",
                "list",
                "--brokers",
                _FIXTURE_BROKERS,
            ],
            cwd=cwd,
            description="kafka readiness probe",
            check=False,
        )
        if result.returncode == 0:
            return
        last_detail = (result.stderr or result.stdout or "").strip() or "no output"
        time.sleep(poll_interval_seconds)
    raise RuntimeError(
        f"kafka fixture did not become ready within {timeout_seconds:.0f}s: {last_detail}",
    )


def _reset_topic(
    base_args: list[str],
    *,
    cwd: Path,
    service: str,
    topic: str,
    partitions: int,
    replication_factor: int,
) -> None:
    _ = _run(
        [
            *base_args,
            "exec",
            "-T",
            service,
            "rpk",
            "topic",
            "delete",
            topic,
            "--brokers",
            _FIXTURE_BROKERS,
        ],
        cwd=cwd,
        description=f"delete kafka topic {topic}",
        check=False,
    )
    _ = _run(
        [
            *base_args,
            "exec",
            "-T",
            service,
            "rpk",
            "topic",
            "create",
            topic,
            "--partitions",
            str(partitions),
            "--replicas",
            str(replication_factor),
            "--brokers",
            _FIXTURE_BROKERS,
        ],
        cwd=cwd,
        description=f"create kafka topic {topic}",
    )


def _publish_dataset(
    base_args: list[str],
    *,
    cwd: Path,
    service: str,
    topic: str,
    input_path: Path,
) -> None:
    process = subprocess.Popen(
        [
            *base_args,
            "exec",
            "-T",
            service,
            "rpk",
            "topic",
            "produce",
            topic,
            "--brokers",
            _FIXTURE_BROKERS,
        ],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        if process.stdin is None:
            raise RuntimeError("failed to open stdin for kafka dataset publisher")
        with input_path.open("rb") as handle:
            for _ in range(_DATASET_REPETITIONS):
                _ = handle.seek(0)
                _ = shutil.copyfileobj(handle, process.stdin)
        process.stdin.close()
        stdout, stderr = process.communicate()
    except Exception:
        process.kill()
        _ = process.wait()
        raise
    if process.returncode == 0:
        return
    detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip() or "no output"
    raise RuntimeError(
        (
            "publish benchmark dataset to kafka topic "
            f"{topic} failed (exit code {process.returncode}): {detail}"
        ),
    )


def _teardown(base_args: list[str], *, cwd: Path) -> None:
    try:
        _ = _run(
            [*base_args, "down", "--volumes", "--remove-orphans"],
            cwd=cwd,
            description="docker compose down",
            check=False,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        _LOG.warning("failed to tear down kafka fixture: %s", exc)


@fixture(name="kafka", replace=True, options=KafkaFixtureOptions)
def kafka() -> FixtureHandle:
    """Start a Kafka-compatible broker and reseed it before every benchmark run."""

    context = current_context()
    if context is None:
        raise RuntimeError("kafka fixture requires an active benchmark context")
    options = current_options("kafka")
    if not isinstance(options, KafkaFixtureOptions):
        raise ValueError("invalid options for fixture 'kafka'")
    if not _compose_available():
        raise FixtureUnavailable("docker compose required but not found")

    compose_file = _resolve_path(context.definition.path, options.compose_file)
    if not compose_file.exists():
        raise RuntimeError(f"kafka compose file does not exist: {compose_file}")

    cwd = compose_file.parent
    base_args = _compose_base_args(
        compose_file=compose_file,
        project_name=_project_name(context.definition.path),
    )
    _ = _run(
        [*base_args, "up", "-d", options.service],
        cwd=cwd,
        description="docker compose up for kafka fixture",
    )
    _ = _wait_for_cluster(
        base_args,
        cwd=cwd,
        service=options.service,
        timeout_seconds=options.wait_timeout_seconds,
        poll_interval_seconds=options.wait_poll_interval_seconds,
    )
    _ = _reset_topic(
        base_args,
        cwd=cwd,
        service=options.service,
        topic=options.topic,
        partitions=options.partitions,
        replication_factor=options.replication_factor,
    )
    _publish_dataset(
        base_args,
        cwd=cwd,
        service=options.service,
        topic=options.topic,
        input_path=context.dataset_path,
    )
    group_prefix = f"{_group_id(context.definition.path, options.topic)}-{uuid.uuid4().hex[:8]}"

    def _before_run(*, phase: str, run_index: int, env: dict[str, str], **_kwargs: object) -> None:
        env["BENCHMARK_KAFKA_GROUP_ID"] = f"{group_prefix}-{phase}-{run_index}"

    return FixtureHandle(
        env={
            "BENCHMARK_KAFKA_BOOTSTRAP_SERVERS": options.bootstrap_servers,
            "BENCHMARK_KAFKA_GROUP_ID": f"{group_prefix}-setup",
            "BENCHMARK_KAFKA_TOPIC": options.topic,
        },
        teardown=lambda: _teardown(base_args, cwd=cwd),
        hooks={"before_run": _before_run},
    )


__all__ = [
    "KafkaFixtureOptions",
    "kafka",
]
