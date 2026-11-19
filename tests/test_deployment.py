import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from platformdirs import PlatformDirs

from tenzir_bench.deployment import DeploymentError, run_pipeline
from tenzir_bench.paths import BenchPaths
from tenzir_bench.runners import DeploymentRunner
from tenzir_bench.runtime import TenzirRuntime, runtime_from_path

# Stands in for a Tenzir build whose `tenzir` runs a deployment when started as
# `tenzir-up`. How a pipeline fares depends on what its definition says.
_FAKE_TENZIR = f"""#!{sys.executable}
import json
import os
import queue
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

assert os.path.basename(sys.argv[0]) == "tenzir-up", sys.argv
assert sys.argv[1] == "--store" and os.path.isdir(sys.argv[2].removeprefix("file:")), sys.argv
inventories = queue.Queue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self.respond(200, {{"status": "ok"}})
        assert self.path == "/pipelines/inventory/live", self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        inventory = []
        while inventory is not None:
            self.wfile.write(f"event: snapshot\\ndata: {{json.dumps(inventory)}}\\n\\n".encode())
            self.wfile.flush()
            inventory = inventories.get()

    def do_POST(self):
        assert self.path == "/pipelines", self.path
        pipeline = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        definition = pipeline["definition"]
        if definition == "refuse":
            return self.respond(400, {{"_tag": "InvalidPipeline", "message": "not a pipeline"}})
        self.send_response(204)
        self.end_headers()
        inventories.put([{{**pipeline, "state": "running", "run": 0, "error": None}}])
        if definition == "fail":
            inventories.put([{{**pipeline, "state": "failed", "run": 0, "error": "it broke"}}])
        elif definition == "finish" or definition in os.environ:
            inventories.put([{{**pipeline, "state": "finished", "run": 0, "error": None}}])


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
signal.signal(signal.SIGINT, lambda *_: os._exit(0))
print(f"serving on http://127.0.0.1:0 (port {{server.server_port}}, HTTP/1.1)", flush=True)
server.serve_forever()
"""


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class DeploymentTest(unittest.TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.root: Path = Path(tmpdir.name)
        tenzir = self.root / "bin" / "tenzir"
        tenzir.parent.mkdir()
        _ = tenzir.write_text(_FAKE_TENZIR, encoding="utf-8")
        tenzir.chmod(0o755)
        self.runtime: TenzirRuntime = runtime_from_path(tenzir)
        paths = BenchPaths(
            dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
            ensure_dir=_ensure,
            cache_root=self.root / "cache",
            state_root=self.root / "state",
        )
        patcher = patch.object(BenchPaths, "create", return_value=paths)
        _ = patcher.start()
        self.addCleanup(patcher.stop)

    def test_measures_a_pipeline_that_runs_to_its_end(self) -> None:
        metrics = run_pipeline(self.runtime, "finish")

        self.assertGreater(metrics.wall_clock, 0)
        self.assertGreater(metrics.max_resident_set_kb, 0)
        self.assertEqual(list((self.root / "state" / "deployments").iterdir()), [])

    def test_starts_the_deployment_with_the_given_environment(self) -> None:
        metrics = run_pipeline(
            self.runtime, "BENCHMARK_INPUT_PATH", env={"BENCHMARK_INPUT_PATH": "/input"}, timeout=30
        )

        self.assertGreater(metrics.wall_clock, 0)

    def test_reports_why_a_pipeline_failed(self) -> None:
        with self.assertRaisesRegex(DeploymentError, "the pipeline failed: it broke"):
            _ = run_pipeline(self.runtime, "fail")

    def test_reports_why_the_deployment_refused_a_pipeline(self) -> None:
        with self.assertRaisesRegex(DeploymentError, "refused the pipeline: not a pipeline"):
            _ = run_pipeline(self.runtime, "refuse")

    def test_gives_up_on_a_pipeline_that_does_not_end_in_time(self) -> None:
        with self.assertRaisesRegex(DeploymentError, "did not end within 0.2s"):
            _ = run_pipeline(self.runtime, "run forever", timeout=0.2)


class DeploymentRunnerTest(unittest.TestCase):
    def test_runs_a_benchmark_in_a_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tenzir = root / "bin" / "tenzir"
            tenzir.parent.mkdir()
            _ = tenzir.write_text(_FAKE_TENZIR, encoding="utf-8")
            tenzir.chmod(0o755)
            pipeline = root / "deployment.tql"
            _ = pipeline.write_text(
                "---\nbench:\n  id: deployment\n---\nfinish\n", encoding="utf-8"
            )
            runner = DeploymentRunner()
            env = {
                **os.environ,
                "PYTHONPATH": os.pathsep.join(sys.path),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CACHE_HOME": str(root / "cache"),
            }

            command = runner.command(runtime_from_path(tenzir), [], pipeline)
            metrics = runner.run(command, env, timeout=30)

        self.assertGreater(metrics.wall_clock, 0)
        self.assertGreater(metrics.max_resident_set_kb, 0)

    def test_refuses_tenzir_options(self) -> None:
        runtime = runtime_from_path(Path("/opt/tenzir/bin/tenzir"))

        with self.assertRaisesRegex(RuntimeError, "takes no Tenzir options, got --neo"):
            _ = DeploymentRunner().command(runtime, ["--neo"], Path("deployment.tql"))
