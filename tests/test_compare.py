import unittest
from pathlib import Path
import tempfile
from platformdirs import PlatformDirs
from unittest.mock import patch

from tenzir_bench.compare import (
    CompareBuild,
    _cache_key,
    _unique_labels,
    expected_report_identities,
    prepare_compare_reports_for_build,
    run_compare,
)
from tenzir_bench.executor import BenchmarkExecutor, BuildInfo, build_result_id
from tenzir_bench.paths import BenchPaths
from tenzir_bench.reports import Report


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
            benchmark = root / "examples" / "benchmarks" / "operators" / "example.tql"
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
                with (
                    unittest.mock.patch.object(
                        BenchmarkExecutor,
                        "_get_build_info",
                        side_effect=AssertionError("build-probe-called"),
                    ),
                    unittest.mock.patch.object(
                        BenchmarkExecutor,
                        "validate",
                        side_effect=AssertionError("validate-called"),
                    ),
                ):
                    run_compare(
                        paths,
                        [
                            CompareBuild(label="base", binary=Path("/tmp/base-tenzir")),
                            CompareBuild(
                                label="candidate",
                                binary=Path("/tmp/candidate-tenzir"),
                                tenzir_args=("--neo",),
                            ),
                        ],
                        compact=False,
                        benchmark_dirs=(benchmark,),
                        dry_run=True,
                    )

    def test_expected_report_identities_respects_version_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            suite = root / "bench" / "benchmarks" / "json_parse"
            suite.mkdir(parents=True)
            (suite / "bench.yaml").write_text(
                """name: json_parse
input:
  path: suricata/eve.json
  events: 1
  measure: true
""",
                encoding="utf-8",
            )
            (suite / "legacy.tql").write_text(
                """---
bench:
  id: legacy
  max_version: "5.29.x"
---
discard
""",
                encoding="utf-8",
            )
            (suite / "neo.tql").write_text(
                """---
bench:
  id: neo
  min_version: "5.30.0"
---
discard
""",
                encoding="utf-8",
            )
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )

            identities = expected_report_identities(
                paths,
                CompareBuild(label="main", binary=None, version="v5.30.1"),
                (suite,),
            )

        self.assertEqual(identities, {("json_parse", "neo")})

    def test_prepare_compare_reports_for_build_reuses_complete_remote_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )
            remote_report = Report(
                path=Path(
                    "s3://bucket/refs/main/abc/static/runner-a/from_kafka_route53/neo/report.json"
                ),
                pipeline="from_kafka_route53/neo",
                benchmark_id="from_kafka_route53",
                implementation_id="neo",
                target="static",
                hardware_key="runner-a",
                wall_clock=1.0,
                rss_kb=1024,
                build_version="v1.2.3",
                artifact_id=None,
            )

            with (
                patch(
                    "tenzir_bench.compare.download_reference_reports",
                    return_value={("from_kafka_route53", "neo"): remote_report},
                ),
                patch(
                    "tenzir_bench.compare.expected_report_identities",
                    return_value={("from_kafka_route53", "neo")},
                ),
                patch("tenzir_bench.compare._prepare_local_reports") as local_reports,
            ):
                reports = prepare_compare_reports_for_build(
                    paths,
                    CompareBuild(
                        label="main",
                        binary=None,
                        reference_destination="s3://bucket/refs/main/abc/static",
                        target="static",
                        version="v1.2.3",
                    ),
                    (),
                )

        self.assertEqual(set(reports), {"from_kafka_route53/neo"})
        local_reports.assert_not_called()

    def test_prepare_compare_reports_for_build_backfills_only_missing_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )
            local_report = Report(
                path=Path("/tmp/report.json"),
                pipeline="from_kafka_route53/neo",
                benchmark_id="from_kafka_route53",
                implementation_id="neo",
                target="static",
                hardware_key="runner-a",
                wall_clock=1.0,
                rss_kb=1024,
                build_version="v1.2.3",
                artifact_id=None,
            )

            with (
                patch("tenzir_bench.compare.download_reference_reports", return_value={}),
                patch(
                    "tenzir_bench.compare.expected_report_identities",
                    return_value={("from_kafka_route53", "neo")},
                ),
                patch(
                    "tenzir_bench.compare._prepare_local_reports",
                    return_value={"from_kafka_route53/neo": local_report},
                ),
                patch("tenzir_bench.compare.Publisher.publish_reports") as publish_reports,
            ):
                reports = prepare_compare_reports_for_build(
                    paths,
                    CompareBuild(
                        label="main",
                        binary=Path("/tmp/tenzir"),
                        reference_destination="s3://bucket/refs/main/abc/static",
                        target="static",
                        version="v1.2.3",
                    ),
                    (),
                )

        self.assertEqual(set(reports), {"from_kafka_route53/neo"})
        publish_reports.assert_called_once()
