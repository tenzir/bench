import unittest
from pathlib import Path
from unittest.mock import patch
import json
import tempfile

from click.testing import CliRunner

from tenzir_bench.cli import (
    _find_repo_root,
    _parse_compare_arguments,
    _render_run_summary,
    main,
)
from tenzir_bench.reports import Report


class CliTest(unittest.TestCase):
    def test_render_run_summary_formats_runtime_and_memory(self) -> None:
        summary = _render_run_summary(
            [
                Report(
                    path=Path("/tmp/report.json"),
                    pipeline="from_kafka_route53/neo-discard",
                    benchmark_id="from_kafka_route53",
                    implementation_id="neo-discard",
                    target="static",
                    hardware_key="local_x86_64_unknown_8c",
                    wall_clock=1.23,
                    rss_kb=65_536,
                    build_version="v1.2.3",
                    artifact_id=None,
                ),
            ],
        )

        self.assertIn("benchmark", summary)
        self.assertIn("runtime", summary)
        self.assertIn("peak rss", summary)
        self.assertIn("from_kafka_route53/neo-discard", summary)
        self.assertIn("1.23s", summary)
        self.assertIn("64 MB", summary)

    def test_parse_compare_arguments_assigns_options_to_last_candidate(self) -> None:
        binaries, benchmarks = _parse_compare_arguments(
            [
                "--base",
                "/tmp/base",
                "--candidate",
                "/tmp/candidate-a",
                "--candidate",
                "/tmp/candidate-b",
                "--neo",
                "benchmarks/operators/suricata-map-ocsf.tql",
            ],
        )

        self.assertEqual(
            binaries,
            [
                ("/tmp/base", False, ()),
                ("/tmp/candidate-a", False, ()),
                ("/tmp/candidate-b", False, ("--neo",)),
            ],
        )
        self.assertEqual(benchmarks, [Path("benchmarks/operators/suricata-map-ocsf.tql")])

    def test_run_forwards_extra_tenzir_args(self) -> None:
        cli_runner = CliRunner()
        with (
            patch("tenzir_bench.cli._resolve_tenzir", return_value=Path("/tmp/tenzir")),
            patch("tenzir_bench.cli._detect_repo_root", return_value=Path("/work/tenzir")),
            patch("tenzir_bench.cli._load_contexts", return_value=[]) as load_contexts,
            patch("tenzir_bench.cli.BenchmarkExecutor") as executor_cls,
        ):
            result = cli_runner.invoke(
                main,
                [
                    "run",
                    "--filter",
                    "benchmarks/operators/suricata-map-ocsf.tql",
                    "--validate",
                    "--verbose",
                    "--neo",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        executor_cls.assert_called_once()
        self.assertEqual(executor_cls.call_args.kwargs["tenzir_args"], ("--neo",))
        self.assertTrue(executor_cls.call_args.kwargs["validate"])
        self.assertFalse(executor_cls.call_args.kwargs["dry_run"])
        self.assertTrue(executor_cls.call_args.kwargs["verbose"])
        self.assertEqual(
            load_contexts.call_args.kwargs["pattern"], "benchmarks/operators/suricata-map-ocsf.tql"
        )

    def test_run_forwards_dry_run(self) -> None:
        cli_runner = CliRunner()
        with (
            patch("tenzir_bench.cli._resolve_tenzir", return_value=Path("/tmp/tenzir")),
            patch("tenzir_bench.cli._detect_repo_root", return_value=Path("/work/tenzir")),
            patch("tenzir_bench.cli._load_contexts", return_value=[]),
            patch("tenzir_bench.cli.BenchmarkExecutor") as executor_cls,
        ):
            result = cli_runner.invoke(
                main,
                [
                    "run",
                    "--filter",
                    "benchmarks/operators/suricata-map-ocsf.tql",
                    "--dry-run",
                    "--verbose",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(executor_cls.call_args.kwargs["dry_run"])
        self.assertFalse(executor_cls.call_args.kwargs["validate"])

    def test_run_auto_detects_root_from_tenzir_binary(self) -> None:
        cli_runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "tenzir"
            cwd = root / "work"
            (root / "bench").mkdir(parents=True)
            cwd.mkdir(parents=True)
            tenzir_bin = root / "build" / "clang" / "relwithdebinfo" / "bin" / "tenzir"
            tenzir_bin.parent.mkdir(parents=True)
            tenzir_bin.write_text("", encoding="utf-8")
            with (
                patch("tenzir_bench.cli.Path.cwd", return_value=cwd),
                patch("tenzir_bench.cli._load_contexts", return_value=[]) as load_contexts,
                patch("tenzir_bench.cli.BenchmarkExecutor"),
            ):
                result = cli_runner.invoke(
                    main,
                    [
                        "run",
                        "--tenzir-bin",
                        str(tenzir_bin),
                        "--benchmark",
                        "from_kafka_route53",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(load_contexts.call_args.kwargs["root"], root)
        self.assertEqual(load_contexts.call_args.kwargs["benchmarks"], ("from_kafka_route53",))

    def test_compare_forwards_dry_run(self) -> None:
        cli_runner = CliRunner()
        with (
            patch("tenzir_bench.cli.resolve_binaries", return_value=[(Path("/tmp/a"), False, ())]),
            patch("tenzir_bench.cli.run_compare") as run_compare,
        ):
            result = cli_runner.invoke(
                main,
                [
                    "compare",
                    "--dry-run",
                    "--base",
                    "/tmp/base",
                    "--candidate",
                    "/tmp/candidate",
                    "benchmarks/operators/suricata-map-ocsf.tql",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(run_compare.call_args.kwargs["dry_run"])
        self.assertFalse(run_compare.call_args.kwargs["validate"])

    def test_run_rejects_conflicting_validate_flags(self) -> None:
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            main,
            [
                "run",
                "--validate",
                "--dry-run",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("mutually exclusive", result.output)

    def test_run_rejects_filter_and_benchmark_together(self) -> None:
        cli_runner = CliRunner()
        result = cli_runner.invoke(
            main,
            [
                "run",
                "--filter",
                "from_kafka_*",
                "--benchmark",
                "from_kafka_route53",
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("mutually exclusive", result.output)

    def test_find_repo_root_walks_upward_to_bench(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "tenzir"
            nested = root / "build" / "clang" / "relwithdebinfo" / "bin"
            (root / "bench").mkdir(parents=True)
            nested.mkdir(parents=True)

            found = _find_repo_root(nested / "tenzir")

        self.assertEqual(found, root)

    def test_run_prints_summary_for_generated_reports(self) -> None:
        cli_runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "pipeline": "from_kafka_route53/neo-discard",
                        "benchmark_id": "from_kafka_route53",
                        "implementation_id": "neo-discard",
                        "target": "static",
                        "hardware": {"key": "local_x86_64_unknown_8c"},
                        "build": {"version": "v1.2.3"},
                        "runtime": {
                            "wall_clock": 1.23,
                            "max_resident_set_kb": 65_536,
                        },
                    },
                ),
                encoding="utf-8",
            )
            with (
                patch("tenzir_bench.cli._resolve_tenzir", return_value=Path("/tmp/tenzir")),
                patch("tenzir_bench.cli._detect_repo_root", return_value=Path("/work/tenzir")),
                patch("tenzir_bench.cli._load_contexts", return_value=[object()]),
                patch("tenzir_bench.cli.BenchmarkExecutor") as executor_cls,
            ):
                executor_cls.return_value.execute.return_value = [report_path]
                result = cli_runner.invoke(
                    main,
                    [
                        "run",
                        "--benchmark",
                        "from_kafka_route53",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("from_kafka_route53/neo-discard", result.output)
        self.assertIn("1.23s", result.output)
        self.assertIn("64 MB", result.output)

    def test_run_reports_when_no_runnable_benchmarks_match(self) -> None:
        cli_runner = CliRunner()
        with (
            patch("tenzir_bench.cli._resolve_tenzir", return_value=Path("/tmp/tenzir")),
            patch("tenzir_bench.cli._detect_repo_root", return_value=Path("/work/tenzir")),
            patch("tenzir_bench.cli._load_contexts", return_value=[]),
            patch("tenzir_bench.cli.BenchmarkExecutor") as executor_cls,
        ):
            executor_cls.return_value.build_info.return_value.build_id = "5.29.2"
            executor_cls.return_value.build_info.return_value.version = "5.29.2"
            result = cli_runner.invoke(
                main,
                [
                    "run",
                    "--benchmark",
                    "from_kafka_route53",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("No runnable benchmarks matched the selection", result.output)
        self.assertIn("5.29.2", result.output)
