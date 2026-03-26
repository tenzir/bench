import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from tenzir_bench import fixtures as fixture_api
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
        self.envs: list[dict[str, str]] = []

    def run(self, command, env, timeout):
        self.calls.append(list(command))
        self.envs.append(dict(env))
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
                fixtures=(),
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

    def test_execute_merges_fixture_env_and_invokes_per_run_hooks(self) -> None:
        @dataclass(frozen=True)
        class DemoOptions:
            topic: str = "default"

        events: list[tuple[str, str, int | str]] = []

        @fixture_api.fixture(name="demo-bench", replace=True, options=DemoOptions)
        def _demo_fixture():
            options = fixture_api.current_options("demo-bench")
            events.append(("options", options.topic, -1))

            def _before_run(*, phase, run_index, env, **_kwargs):  # noqa: ANN001
                events.append(("before", phase, run_index))
                assert env["DEMO_FIXTURE"] == f"fixture-{options.topic}"

            def _after_run(*, phase, run_index, success, **_kwargs):  # noqa: ANN001
                events.append(("after", phase, run_index))
                assert success is True

            return fixture_api.FixtureHandle(
                env={"DEMO_FIXTURE": f"fixture-{options.topic}"},
                hooks={"before_run": _before_run, "after_run": _after_run},
            )

        try:
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
                    path=root / "benchmarks" / "operators" / "fixture-example.tql",
                    id="fixture-benchmark",
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
                    fixtures=(
                        fixture_api.FixtureSpec(
                            name="demo-bench",
                            options={"topic": "bench"},
                        ),
                    ),
                    tenzir_args=[],
                    runner="time",
                    runtime=BenchmarkRuntime(warmup_runs=1, measurement_runs=2, timeout_seconds=10),
                    pipeline_body="discard",
                )
                definition.path.parent.mkdir(parents=True, exist_ok=True)
                definition.path.write_text("discard\n", encoding="utf-8")
                runner = FakeRunner()
                executor = BenchmarkExecutor(
                    paths,
                    Path("/tmp/tenzir-link"),
                    RunnerRegistry([runner]),
                    verbose=True,
                )
                context = executor.create_context(definition)
                assert context is not None

                stdout = io.StringIO()
                with (
                    patch("tenzir_bench.executor._detect_build", return_value=BuildInfo("v1.2.3", "Release", "/tmp/tenzir")),
                    patch.object(BenchmarkExecutor, "_validate_invocation", return_value=None),
                    patch("tenzir_bench.executor._git_revision", return_value="deadbeef"),
                    patch("tenzir_bench.executor._LOG.info"),
                    redirect_stdout(stdout),
                ):
                    reports = executor.execute(context)
        finally:
            fixture_api._FACTORIES.pop("demo-bench", None)  # type: ignore[attr-defined]
            fixture_api._OPTIONS_CLASSES.pop("demo-bench", None)  # type: ignore[attr-defined]

        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(len(runner.envs), 3)
        self.assertEqual(len(reports), 2)
        for env in runner.envs:
            self.assertEqual(env["DEMO_FIXTURE"], "fixture-bench")
            self.assertIn("DEMO_FIXTURE", env["TENZIR_BENCH_FORWARD_ENV"].split(","))
        self.assertEqual(
            [event for event in events if event[0] == "before"],
            [
                ("before", "warmup", 0),
                ("before", "measurement", 0),
                ("before", "measurement", 1),
            ],
        )
        self.assertEqual(
            [event for event in events if event[0] == "after"],
            [
                ("after", "warmup", 0),
                ("after", "measurement", 0),
                ("after", "measurement", 1),
            ],
        )
        self.assertEqual([event for event in events if event[0] == "options"], [("options", "bench", -1)])
        output = stdout.getvalue()
        self.assertIn("DEMO_FIXTURE=fixture-bench", output)
