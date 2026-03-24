import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from platformdirs import PlatformDirs

from tenzir_bench.definitions import BenchmarkDefinition, BenchmarkRuntime
from tenzir_bench.executor import BenchmarkExecutor, BuildInfo
from tenzir_bench.paths import BenchPaths
from tenzir_bench.runners import Runner, RunnerMetrics, RunnerRegistry


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class FakeRunner(Runner):
    name = "time"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, command, env, timeout):
        self.calls.append(list(command))
        return RunnerMetrics(
            wall_clock=1.0,
            cpu_user=0.1,
            cpu_system=0.1,
            max_resident_set_kb=1024,
        )


class ExecutorTest(unittest.TestCase):
    def test_verbose_prints_command_once_per_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )
            dataset = paths.datasets_cache_dir / "suricata" / "eve.json"
            dataset.parent.mkdir(parents=True, exist_ok=True)
            dataset.write_text('{"event_type":"flow"}\n', encoding="utf-8")
            definition = BenchmarkDefinition(
                path=Path("benchmarks/operators/example.tql"),
                id="example-benchmark",
                description=None,
                tags={},
                min_version=None,
                max_version=None,
                input_path="suricata/eve.json",
                input_events=1,
                input_measure=True,
                output_path=None,
                output_measure=False,
                env={"TENZIR_CONSOLE_FORMAT": "none"},
                tenzir_args=["--neo"],
                runner="time",
                runtime=BenchmarkRuntime(warmup_runs=1, measurement_runs=3, timeout_seconds=10),
                pipeline_body="discard",
            )
            runner = FakeRunner()
            executor = BenchmarkExecutor(
                paths,
                Path("/tmp/tenzir-link"),
                RunnerRegistry([runner]),
                tenzir_args=("--global-flag", "--neo"),
                verbose=True,
            )
            context = executor.create_context(definition)
            assert context is not None

            stdout = io.StringIO()
            with (
                patch("tenzir_bench.executor._detect_build", return_value=BuildInfo("v1.2.3", "Release", "/tmp/tenzir")),
                patch.object(BenchmarkExecutor, "_validate_invocation", return_value=None),
                patch("tenzir_bench.executor._LOG.info"),
                redirect_stdout(stdout),
            ):
                reports = executor.execute(context)

        output = stdout.getvalue()
        self.assertEqual(output.count("# example-benchmark (/tmp/tenzir-link --global-flag --neo)"), 1)
        self.assertIn("env BENCHMARK_INPUT_PATH=", output)
        self.assertIn("benchmarks/operators/example.tql", output)
        self.assertNotIn("/_pipelines/", output)
        self.assertEqual(len(runner.calls), 4)
        self.assertEqual(len(reports), 3)
