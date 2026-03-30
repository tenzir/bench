import unittest
from pathlib import Path
import tempfile
from platformdirs import PlatformDirs

from tenzir_bench.compare import _cache_key, _unique_labels, run_compare
from tenzir_bench.executor import BenchmarkExecutor, BuildInfo, build_result_id
from tenzir_bench.paths import BenchPaths


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class CompareHelpersTest(unittest.TestCase):
    def test_cache_key_distinguishes_same_version_binaries(self) -> None:
        info_a = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")
        info_b = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/b/bin/tenzir")

        cache_a = _cache_key(info_a, Path("/tmp/a/bin/tenzir"), ())
        cache_b = _cache_key(info_b, Path("/tmp/b/bin/tenzir"), ())

        self.assertNotEqual(cache_a, cache_b)

    def test_cache_key_distinguishes_same_binary_with_different_options(self) -> None:
        info = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")

        cache_a = _cache_key(info, Path("/tmp/a/bin/tenzir"), ())
        cache_b = _cache_key(info, Path("/tmp/a/bin/tenzir"), ("--neo",))

        self.assertNotEqual(cache_a, cache_b)

    def test_duplicate_labels_get_disambiguated(self) -> None:
        labels = _unique_labels(
            ["v1.2.3", "v1.2.3"],
            [Path("/tmp/a/bin/tenzir"), Path("/tmp/b/bin/tenzir")],
        )

        self.assertEqual(len(set(labels)), 2)
        self.assertTrue(all(label.startswith("v1.2.3[") for label in labels))

    def test_build_result_id_distinguishes_forwarded_tenzir_args(self) -> None:
        info = BuildInfo(version="v1.2.3", build_type="Release", path="/tmp/a/bin/tenzir")

        self.assertEqual(build_result_id(info, ()), "v1.2.3")
        self.assertNotEqual(build_result_id(info, ("--neo",)), "v1.2.3")

    def test_run_compare_dry_run_skips_build_probes(self) -> None:
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
            benchmark = root / "benchmarks" / "operators" / "example.tql"
            benchmark.parent.mkdir(parents=True, exist_ok=True)
            benchmark.write_text(
                """---
benchmark:
  id: example
  input:
    path: suricata/eve.json
    events: 1
    measure: true
---
discard
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(AssertionError, "validate-called"):
                with unittest.mock.patch.object(
                    BenchmarkExecutor,
                    "_get_build_info",
                    side_effect=AssertionError("build-probe-called"),
                ), unittest.mock.patch.object(
                    BenchmarkExecutor,
                    "validate",
                    side_effect=AssertionError("validate-called"),
                ):
                    run_compare(
                        paths,
                        [
                            (Path("/tmp/base-tenzir"), False, ()),
                            (Path("/tmp/candidate-tenzir"), False, ("--neo",)),
                        ],
                        compact=False,
                        benchmark_dirs=(benchmark,),
                        dry_run=True,
                    )
