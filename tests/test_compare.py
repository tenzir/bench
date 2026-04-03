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
from tenzir_bench.runtime import _docker_wrapper_script, runtime_from_path


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class CompareHelpersTest(unittest.TestCase):
    def test_runtime_from_path_preserves_tenzir_node_symlink_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bindir = Path(tmpdir) / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            tenzir = bindir / "tenzir"
            tenzir.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            tenzir.chmod(0o755)
            tenzir_node = bindir / "tenzir-node"
            tenzir_node.symlink_to(tenzir.name)

            runtime = runtime_from_path(tenzir)

        self.assertEqual(runtime.command_for_tenzir_node(())[0], str(tenzir_node))

    def test_runtime_from_path_recovers_docker_node_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            docker_dir = Path(tmpdir) / "state" / "docker"
            docker_dir.mkdir(parents=True, exist_ok=True)
            tenzir_wrapper = docker_dir / "example-1234.sh"
            tenzir_wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            tenzir_wrapper.chmod(0o755)
            tenzir_node_wrapper = docker_dir / "example-1234-node.sh"
            tenzir_node_wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            tenzir_node_wrapper.chmod(0o755)

            runtime = runtime_from_path(tenzir_wrapper)

        self.assertEqual(runtime.target, "docker")
        self.assertEqual(runtime.command_for_tenzir(()), [str(tenzir_wrapper)])
        self.assertEqual(
            runtime.command_for_tenzir_node(()),
            [str(tenzir_node_wrapper)],
        )

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
  inputs:
    main:
      path: suricata/eve.json
      repetitions: 1
      source:
        num_events: 1
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
inputs:
  main:
    path: suricata/eve.json
    repetitions: 1
    source:
      num_events: 1
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

    def test_prepare_compare_reports_for_build_filters_unrequested_remote_benchmarks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )
            requested = Report(
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
            unrelated = Report(
                path=Path(
                    "s3://bucket/refs/main/abc/static/runner-a/from_file_route53_ocsf/neo/report.json"
                ),
                pipeline="from_file_route53_ocsf/neo",
                benchmark_id="from_file_route53_ocsf",
                implementation_id="neo",
                target="static",
                hardware_key="runner-a",
                wall_clock=2.0,
                rss_kb=2048,
                build_version="v1.2.3",
                artifact_id=None,
            )

            with (
                patch(
                    "tenzir_bench.compare.download_reference_reports",
                    return_value={
                        ("from_kafka_route53", "neo"): requested,
                        ("from_file_route53_ocsf", "neo"): unrelated,
                    },
                ),
                patch(
                    "tenzir_bench.compare.expected_report_identities",
                    return_value={("from_kafka_route53", "neo")},
                ),
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

    def test_prepare_compare_reports_for_build_refreshes_stale_docker_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )
            stale_remote = Report(
                path=Path(
                    "s3://bucket/refs/main/abc/docker/runner-a/from_kafka_route53/neo/report.json"
                ),
                pipeline="from_kafka_route53/neo",
                benchmark_id="from_kafka_route53",
                implementation_id="neo",
                target="docker",
                hardware_key="runner-a",
                wall_clock=1.0,
                rss_kb=30_000,
                build_version="v1.2.3",
                artifact_id=None,
                schema_version=1,
            )
            refreshed_local = Report(
                path=Path("/tmp/report.json"),
                pipeline="from_kafka_route53/neo",
                benchmark_id="from_kafka_route53",
                implementation_id="neo",
                target="docker",
                hardware_key="runner-a",
                wall_clock=1.0,
                rss_kb=400_000,
                build_version="v1.2.3",
                artifact_id=None,
            )

            with (
                patch(
                    "tenzir_bench.compare.download_reference_reports",
                    return_value={("from_kafka_route53", "neo"): stale_remote},
                ),
                patch(
                    "tenzir_bench.compare.expected_report_identities",
                    return_value={("from_kafka_route53", "neo")},
                ),
                patch(
                    "tenzir_bench.compare._prepare_local_reports",
                    return_value={"from_kafka_route53/neo": refreshed_local},
                ) as local_reports,
                patch("tenzir_bench.compare.Publisher.publish_reports") as publish_reports,
            ):
                reports = prepare_compare_reports_for_build(
                    paths,
                    CompareBuild(
                        label="main",
                        binary=Path("/tmp/tenzir"),
                        reference_destination="s3://bucket/refs/main/abc/docker",
                        target="docker",
                        version="v1.2.3",
                    ),
                    (),
                )

        self.assertEqual(set(reports), {"from_kafka_route53/neo"})
        local_reports.assert_called_once()
        publish_reports.assert_called_once_with(
            {"from_kafka_route53/neo": refreshed_local},
            "s3://bucket/refs/main/abc/docker",
            force=True,
        )

    def test_docker_wrapper_runs_python_entrypoint_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = BenchPaths(
                dirs=PlatformDirs(appname="tenzir-bench", appauthor="Tenzir"),
                ensure_dir=_ensure,
                cache_root=root / "cache",
                state_root=root / "state",
            )

            script = _docker_wrapper_script("ghcr.io/tenzir/tenzir:test", paths)

        self.assertTrue(script.startswith("#!/usr/bin/env bash\n"))
        self.assertIn(
            "docker image inspect --format '{{json .Config.Entrypoint}}' \"$IMAGE\"", script
        )
        self.assertIn("docker image inspect --format '{{json .Config.Cmd}}' \"$IMAGE\"", script)
        self.assertIn('if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then', script)
        self.assertIn('docker pull "$IMAGE" >&2', script)
        self.assertIn("--entrypoint python3", script)
        self.assertIn("tenzir-bench-maxrss=", script)
        self.assertIn("os.wait4(proc.pid, 0)", script)
        self.assertIn('forward_envs+=("-e" "CACHE_DIRECTORY=$CACHE_DIR")', script)
        self.assertIn('forward_envs+=("-e" "STATE_DIRECTORY=$STATE_DIR")', script)
        self.assertIn('forward_envs+=("-e" "LOGS_DIRECTORY=$LOGS_DIR")', script)
