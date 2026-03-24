import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from tenzir_bench.cli import _parse_compare_arguments, main


class CliTest(unittest.TestCase):
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
            patch("tenzir_bench.cli.BenchmarkExecutor") as executor_cls,
        ):
            executor = executor_cls.return_value
            executor.discover.return_value = []

            result = cli_runner.invoke(
                main,
                [
                    "run",
                    "--filter",
                    "benchmarks/operators/suricata-map-ocsf.tql",
                    "--dry-run",
                    "--verbose",
                    "--neo",
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        executor_cls.assert_called_once()
        self.assertEqual(executor_cls.call_args.kwargs["tenzir_args"], ("--neo",))
        self.assertTrue(executor_cls.call_args.kwargs["dry_run"])
        self.assertTrue(executor_cls.call_args.kwargs["verbose"])
