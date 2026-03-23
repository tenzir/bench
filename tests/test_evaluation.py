import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tenzir_bench.evaluation import _print_detailed
from tenzir_bench.reports import Report


class EvaluationOutputTest(unittest.TestCase):
    def test_detailed_output_handles_missing_candidate(self) -> None:
        baseline = Report(
            path=Path("/tmp/base.json"),
            pipeline="bench/example",
            wall_clock=1.25,
            rss_kb=2048,
            build_version="v1.0.0",
            artifact_id="v1.0.0",
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            _print_detailed(["bench/example"], {}, {"bench/example": baseline}, {})

        output = stdout.getvalue()
        self.assertIn("Candidate: missing", output)
        self.assertIn("Δ=- (-)", output)
        self.assertIn("Δrss=-", output)
