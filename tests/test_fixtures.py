import tempfile
import unittest
from pathlib import Path

from tenzir_bench import fixtures as fixture_api


class FixtureLoadingTest(unittest.TestCase):
    def test_load_fixture_modules_imports_nearby_fixtures_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            benchmark = root / "benchmarks" / "integrations" / "from-kafka.tql"
            benchmark.parent.mkdir(parents=True, exist_ok=True)
            benchmark.write_text("discard\n", encoding="utf-8")
            fixture_file = benchmark.parent / "fixtures.py"
            fixture_file.write_text(
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

        fixture_api._FACTORIES.pop("auto-loaded", None)  # type: ignore[attr-defined]
