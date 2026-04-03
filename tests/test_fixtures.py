import tempfile
import unittest
from pathlib import Path
from typing import TextIO, cast, final

from tenzir_bench.runtime import TenzirRuntime

from tenzir_bench import fixtures as fixture_api


def _fixture_factories() -> dict[str, object]:
    return cast(dict[str, object], getattr(fixture_api, "_FACTORIES"))


class FixtureLoadingTest(unittest.TestCase):
    def test_load_fixture_modules_imports_nearby_fixtures_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            benchmark = root / "examples" / "benchmarks" / "integrations" / "from-kafka.tql"
            benchmark.parent.mkdir(parents=True, exist_ok=True)
            _ = benchmark.write_text("discard\n", encoding="utf-8")
            fixture_file = benchmark.parent / "fixtures.py"
            _ = fixture_file.write_text(
                """from tenzir_bench.fixtures import fixture

@fixture(name="auto-loaded", replace=True)
def auto_loaded_fixture():
    return {"AUTO_LOADED_FIXTURE": "ok"}
""",
                encoding="utf-8",
            )

            fixture_api.load_fixture_modules(benchmark, root=root)
            with fixture_api.activate((fixture_api.FixtureSpec(name="auto-loaded"),)) as env:
                self.assertEqual(env["AUTO_LOADED_FIXTURE"], "ok")

        _ = _fixture_factories().pop("auto-loaded", None)

    def test_load_fixture_modules_imports_shared_bench_fixture_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            benchmark = root / "bench" / "benchmarks" / "integrations" / "from-kafka.tql"
            benchmark.parent.mkdir(parents=True, exist_ok=True)
            _ = benchmark.write_text("discard\n", encoding="utf-8")
            fixtures_dir = root / "bench" / "fixtures" / "shared"
            fixtures_dir.mkdir(parents=True, exist_ok=True)
            fixture_file = fixtures_dir / "fixture.py"
            _ = fixture_file.write_text(
                """from tenzir_bench.fixtures import fixture

@fixture(name="shared-bench", replace=True)
def shared_bench_fixture():
    return {"SHARED_BENCH_FIXTURE": "ok"}
""",
                encoding="utf-8",
            )

            fixture_api.load_fixture_modules(benchmark, root=root)
            with fixture_api.activate((fixture_api.FixtureSpec(name="shared-bench"),)) as env:
                self.assertEqual(env["SHARED_BENCH_FIXTURE"], "ok")

        _ = _fixture_factories().pop("shared-bench", None)

    def test_node_catalog_lookup_uses_runtime_api(self) -> None:
        @final
        class FakeProcess:
            def __init__(self) -> None:
                self.signals: list[int] = []
                self.wait_calls: int = 0

            def poll(self) -> None:
                return None

            def send_signal(self, signum: int) -> None:
                self.signals.append(signum)

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                self.wait_calls += 1
                return 0

            def kill(self) -> None:
                raise AssertionError("fixture should not need to kill the fake node")

        @final
        class FakeRuntime:
            def __init__(self) -> None:
                self.node_calls: list[list[str]] = []
                self.run_calls: list[list[str]] = []
                self.process: FakeProcess = FakeProcess()

            def popen_tenzir_node(
                self,
                *,
                args: list[str],
                env: dict[str, str] | None = None,
                stdout: object = None,
                stderr: object = None,
                text: bool = True,
                cwd: Path | None = None,
            ) -> FakeProcess:
                self.node_calls.append(args)
                assert env == {"TENZIR_CONSOLE_FORMAT": "none"}
                assert stderr is not None
                assert text is True
                assert cwd is None
                assert stdout is not None
                handle = cast(TextIO, stdout)
                _ = handle.write("127.0.0.1:5151\n")
                handle.flush()
                return self.process

            def run_tenzir(
                self,
                *,
                args: list[str],
                env: dict[str, str] | None = None,
                capture_output: bool = False,
                text: bool = True,
                check: bool = False,
                cwd: Path | None = None,
            ) -> object:
                self.run_calls.append(args)
                assert env == {"TENZIR_CONSOLE_FORMAT": "none"}
                assert capture_output is True
                assert text is True
                assert check is False
                assert cwd is not None

                @final
                class Result:
                    returncode: int = 0
                    stdout: str = ""
                    stderr: str = ""

                return Result()

        @final
        class DefinitionStub:
            def __init__(self, path: Path) -> None:
                self.path: Path = path

        fixture_api.load_fixture_modules(
            Path("examples/benchmarks/suricata_node_catalog_lookup/neo.tql"),
            root=Path.cwd(),
        )

        runtime = FakeRuntime()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset_path = root / "datasets" / "suricata-dns.seed.ndjson"
            expected_state_dir = root / "output" / "node-catalog-lookup" / "state"
            context = fixture_api.FixtureContext(
                definition=DefinitionStub(
                    Path("examples/benchmarks/suricata_node_catalog_lookup/neo.tql")
                ),
                dataset_path=dataset_path,
                output_root=root / "output",
                env={},
                runtime=cast(TenzirRuntime, cast(object, runtime)),
            )
            token = fixture_api.push_context(context)
            try:
                with fixture_api.activate(
                    (
                        fixture_api.FixtureSpec(
                            name="node_catalog_lookup",
                            options={
                                "events": 8,
                                "query_hit_index": 3,
                            },
                        ),
                    )
                ) as env:
                    self.assertEqual(env["TENZIR_ENDPOINT"], "127.0.0.1:5151")
                    self.assertEqual(env["BENCHMARK_LOOKUP_VALUE"], "bench-000003.example")
                    self.assertTrue(dataset_path.exists())
            finally:
                fixture_api.pop_context(token)

        self.assertEqual(
            runtime.node_calls[0][:4],
            ["-d", str(expected_state_dir), "--endpoint=127.0.0.1:0", "--print-endpoint"],
        )
        self.assertIn("-f", runtime.run_calls[0])
        self.assertEqual(runtime.process.wait_calls, 1)
