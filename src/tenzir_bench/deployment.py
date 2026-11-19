"""Run one pipeline in a deployment of its own and report what the run cost.

A deployment runs pipelines behind an API instead of as one process per
pipeline. To measure a pipeline there, this module starts a deployment that
nothing else uses, hands it the pipeline, waits until the pipeline has ended,
and stops the deployment again.

The module is also a program, which is how the `deployment` runner uses it:

    python -m tenzir_bench.deployment --tenzir /path/to/tenzir --file pipeline.tql

It prints the measurements as one JSON object.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import closing, suppress
from pathlib import Path
from typing import Final, cast

from .definitions import split_frontmatter
from .paths import BenchPaths
from .runners import RunnerMetrics
from .runtime import DEPLOYMENT_RUSAGE_MARKER, TenzirRuntime, runtime_from_path

_HOST: Final = "127.0.0.1"
_PIPELINE_ID: Final = "benchmark"
# A deployment reports the port it bound, so it can pick a free one itself.
_PORT_RE: Final = re.compile(r"\(port (?P<port>\d+)")
_RUSAGE_RE: Final = re.compile(
    rf"^{re.escape(DEPLOYMENT_RUSAGE_MARKER)}(?P<user>\S+) (?P<system>\S+) (?P<maxrss>\d+)$",
    re.MULTILINE,
)
# The states in which a pipeline does not run and does not start again.
_ENDED: Final = frozenset({"finished", "failed", "stopped"})
_STARTUP_TIMEOUT: Final = 60.0
_REQUEST_TIMEOUT: Final = 60.0
# How long a deployment gets to stop before it is asked again, more bluntly.
_STOP_TIMEOUT: Final = 30.0
_POLL_INTERVAL: Final = 0.01


class DeploymentError(RuntimeError):
    """Raised when a pipeline could not be measured in a deployment."""


class PipelineError(DeploymentError):
    """Raised when the deployment worked, but the pipeline did not run to its end."""


def run_pipeline(
    runtime: TenzirRuntime,
    pipeline: str,
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
) -> RunnerMetrics:
    """Run `pipeline` to its end in a new deployment of `runtime`.

    The wall-clock time spans from handing the pipeline to the deployment
    until the deployment reports that it ended. The CPU time and the peak
    resident set size are those of the deployment from its start to its exit.

    The deployment keeps its state in the benchmark state directory, which
    Docker targets can reach as well.
    """
    scratch_root = BenchPaths.create().state_dir / "deployments"
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch_root) as scratch:
        store = Path(scratch) / "store"
        store.mkdir()
        stdout_path = Path(scratch) / "stdout.log"
        stderr_path = Path(scratch) / "stderr.log"
        with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
            process = runtime.popen_deployment(
                args=["--store", f"file:{store}", f"{_HOST}:0"],
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
        deployment = _Deployment(process)
        try:
            try:
                port = _await_port(deployment, stdout_path)
                _await_health(deployment, port)
                wall_clock = _measure(port, pipeline, timeout)
            finally:
                deployment.stop()
        except PipelineError:
            raise
        except DeploymentError as exc:
            # The deployment itself is at fault, so what it logged may say why.
            log = stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            if log:
                raise DeploymentError(f"{exc}\n{log}") from exc
            raise
        usage = deployment.usage(
            stderr_path.read_text(encoding="utf-8", errors="replace"),
            reported=runtime.target == "docker",
        )
    return RunnerMetrics(
        wall_clock=wall_clock,
        cpu_user=usage[0],
        cpu_system=usage[1],
        max_resident_set_kb=usage[2],
    )


class _Deployment:
    """A deployment process, reaped in a way that keeps its resource usage."""

    def __init__(self, process: subprocess.Popen[str]) -> None:
        self._process: subprocess.Popen[str] = process
        self._rusage: tuple[float, float, int] | None = None

    def exited(self) -> bool:
        return self._reap(os.WNOHANG)

    def stop(self) -> None:
        """Stop the deployment: gracefully, then at once, then by force."""
        for signum in (signal.SIGINT, signal.SIGINT, signal.SIGKILL):
            if self.exited():
                return
            self._process.send_signal(signum)
            deadline = time.monotonic() + _STOP_TIMEOUT
            while time.monotonic() < deadline:
                if self.exited():
                    return
                time.sleep(_POLL_INTERVAL)
        _ = self._reap(0)

    def usage(self, stderr: str, *, reported: bool) -> tuple[float, float, int]:
        """Return the user and system CPU seconds and the peak RSS in kilobytes.

        A deployment that runs in a container reports its usage itself, as the
        process that started the container says nothing about it.
        """
        if not reported:
            assert self._rusage is not None
            return self._rusage
        match = _RUSAGE_RE.search(stderr)
        if match is None:
            raise DeploymentError("the deployment did not report its resource usage")
        return float(match["user"]), float(match["system"]), int(match["maxrss"])

    def _reap(self, options: int) -> bool:
        process = self._process
        if process.returncode is not None:
            return True
        pid, status, rusage = os.wait4(process.pid, options)
        if pid == 0:
            return False
        process.returncode = os.waitstatus_to_exitcode(status)
        # macOS reports the peak resident set size in bytes.
        scale = 1024 if sys.platform == "darwin" else 1
        self._rusage = (rusage.ru_utime, rusage.ru_stime, rusage.ru_maxrss // scale)
        return True


def _await_port(deployment: _Deployment, stdout_path: Path) -> int:
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        match = _PORT_RE.search(stdout_path.read_text(encoding="utf-8", errors="replace"))
        if match is not None:
            return int(match["port"])
        if deployment.exited():
            raise DeploymentError("the deployment exited before it served its API")
        time.sleep(_POLL_INTERVAL)
    raise DeploymentError(f"the deployment did not serve its API within {_STARTUP_TIMEOUT:.0f}s")


def _await_health(deployment: _Deployment, port: int) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            status, _ = _request(port, "GET", "/health")
        except (OSError, http.client.HTTPException):
            status = None
        if status == http.client.OK:
            return
        if deployment.exited():
            raise DeploymentError("the deployment exited before it was ready")
        time.sleep(_POLL_INTERVAL)
    raise DeploymentError(f"the deployment was not ready within {_STARTUP_TIMEOUT:.0f}s")


def _measure(port: int, pipeline: str, timeout: float | None) -> float:
    """Run the pipeline and return how long that took, in seconds."""
    connection = http.client.HTTPConnection(_HOST, port, timeout=_REQUEST_TIMEOUT)
    try:
        connection.connect()
    except OSError as exc:
        raise DeploymentError(f"failed to reach the deployment: {exc!r}") from exc
    stream = cast(socket.socket, connection.sock)
    try:
        return _measure_on(connection, stream, port, pipeline, timeout)
    finally:
        # End the stream before closing the connection: closing waits for the
        # thread that reads the stream, which waits for the deployment.
        with suppress(OSError):
            stream.shutdown(socket.SHUT_RDWR)
        connection.close()


def _measure_on(
    connection: http.client.HTTPConnection,
    stream: socket.socket,
    port: int,
    pipeline: str,
    timeout: float | None,
) -> float:
    try:
        # The deployment sends every change of its pipelines, starting with
        # what it has right now. Waiting for that first message before
        # creating the pipeline ensures that none of its changes is missed.
        connection.request(
            "GET", "/pipelines/inventory/live", headers={"Accept": "text/event-stream"}
        )
        response = connection.getresponse()
        if response.status != http.client.OK:
            raise DeploymentError(
                f"the deployment does not stream its pipelines: {_describe(response)}"
            )
        # A pipeline may run for long without any change to report.
        stream.settimeout(None)
        inventories = _Inventories(response)
        _ = inventories.next(_REQUEST_TIMEOUT)
        started = time.perf_counter()
        status, body = _request(
            port,
            "POST",
            "/pipelines",
            {"id": _PIPELINE_ID, "name": _PIPELINE_ID, "definition": pipeline},
        )
    except (OSError, http.client.HTTPException) as exc:
        raise DeploymentError(f"failed to reach the deployment: {exc!r}") from exc
    if status >= http.client.BAD_REQUEST:
        raise PipelineError(f"the deployment refused the pipeline: {_message(body)}")
    while True:
        remaining = None if timeout is None else started + timeout - time.perf_counter()
        try:
            received, inventory = inventories.next(remaining)
        except TimeoutError:
            raise PipelineError(f"the pipeline did not end within {timeout:g}s") from None
        state = _pipeline_state(inventory)
        if state is None or state.get("state") not in _ENDED:
            continue
        if state["state"] != "finished":
            raise PipelineError(
                f"the pipeline {state['state']}: {state.get('error') or 'no error given'}"
            )
        return received - started


class _Inventories:
    """The pipeline inventories a deployment sends, as server-sent events.

    A thread reads them, so that waiting for the next one can time out however
    the deployment paces what it sends.
    """

    def __init__(self, response: http.client.HTTPResponse) -> None:
        self._response: http.client.HTTPResponse = response
        self._received: queue.SimpleQueue[tuple[float, list[object]] | Exception] = (
            queue.SimpleQueue()
        )
        threading.Thread(target=self._read, daemon=True).start()

    def next(self, timeout: float | None) -> tuple[float, list[object]]:
        """Return the next inventory and when it arrived."""
        try:
            if timeout is not None and timeout <= 0:
                raise queue.Empty
            received = self._received.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None
        if isinstance(received, Exception):
            raise DeploymentError(
                f"the deployment stopped reporting its pipelines: {received!r}"
            ) from received
        return received

    def _read(self) -> None:
        data: list[bytes] = []
        try:
            while line := self._response.readline():
                line = line.rstrip(b"\r\n")
                if line.startswith(b"data:"):
                    data.append(line[len(b"data:") :].lstrip(b" "))
                elif not line and data:
                    received = time.perf_counter()
                    payload = cast(object, json.loads(b"\n".join(data)))
                    data = []
                    if isinstance(payload, list):
                        self._received.put((received, cast(list[object], payload)))
            self._received.put(EOFError("the stream ended"))
        except Exception as exc:
            self._received.put(exc)


def _pipeline_state(inventory: Sequence[object]) -> dict[str, object] | None:
    for entry in inventory:
        if isinstance(entry, dict):
            pipeline = cast(dict[str, object], entry)
            if pipeline.get("id") == _PIPELINE_ID:
                return pipeline
    return None


def _request(
    port: int,
    method: str,
    path: str,
    payload: Mapping[str, object] | None = None,
) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(_HOST, port, timeout=_REQUEST_TIMEOUT)
    with closing(connection):
        body = None if payload is None else json.dumps(payload)
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()


def _describe(response: http.client.HTTPResponse) -> str:
    return f"{response.status} {_message(response.read())}"


def _message(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace").strip()
    try:
        payload = cast(object, json.loads(text))
    except ValueError:
        return text
    if isinstance(payload, dict):
        message = cast(dict[str, object], payload).get("message")
        if isinstance(message, str):
            return message
    return text


def _read_pipeline(path: Path) -> str:
    """Return the pipeline in `path`, without the frontmatter a benchmark has."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        return split_frontmatter(text, path)[1]
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tenzir_bench.deployment",
        description="Run a pipeline in a deployment of its own and print what the run cost.",
    )
    _ = parser.add_argument(
        "--tenzir",
        required=True,
        type=Path,
        help="the Tenzir executable, or the wrapper tenzir-bench wrote for a Docker image",
    )
    _ = parser.add_argument("--file", required=True, type=Path, help="the pipeline to run")
    _ = parser.add_argument(
        "--timeout", type=float, help="how many seconds the pipeline may take to end"
    )
    arguments = parser.parse_args(argv)
    # Leave through the cleanup that stops the deployment.
    _ = signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        metrics = run_pipeline(
            runtime_from_path(cast(Path, arguments.tenzir)),
            _read_pipeline(cast(Path, arguments.file)),
            timeout=cast("float | None", arguments.timeout),
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    json.dump(
        {
            "wall_clock": metrics.wall_clock,
            "cpu_user": metrics.cpu_user,
            "cpu_system": metrics.cpu_system,
            "max_resident_set_kb": metrics.max_resident_set_kb,
        },
        sys.stdout,
    )
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
