import json
import tempfile
import unittest
from pathlib import Path

from tenzir_bench.reports import load_report, load_reports, matches_identifier


def _write_report(root: Path, artifact_id: str, pipeline: str) -> None:
    file_name = pipeline.replace("/", "__")
    path = root / "suite" / "input" / artifact_id / "time" / f"{file_name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pipeline": pipeline,
        "benchmark_id": pipeline.split("/")[0],
        "implementation_id": pipeline.split("/")[-1],
        "build": {"version": artifact_id},
        "runtime": {
            "wall_clock": 1.0,
            "max_resident_set_kb": 1024,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class ReportsTest(unittest.TestCase):
    def test_load_reports_filters_by_artifact_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_report(root, "v1.2.3", "bench/release")
            _write_report(root, "main-abcdef1", "bench/main")

            reports = load_reports(root, artifact_filter="v1.2.3")

        self.assertEqual(set(reports), {"bench/release"})

    def test_matches_identifier_accepts_commitish_prefixes(self) -> None:
        self.assertTrue(matches_identifier("main-abcdef1", "abcdef1234567890"))
        self.assertFalse(matches_identifier("v1.2.3", "abcdef1234567890"))

    def test_load_reports_preserves_benchmark_and_implementation_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_report(root, "v1.2.3", "from_kafka_1m/neo")

            reports = load_reports(root)

        report = reports["from_kafka_1m/neo"][0]
        self.assertEqual(report.benchmark_id, "from_kafka_1m")
        self.assertEqual(report.implementation_id, "neo")

    def test_load_report_reads_single_report_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_report(root, "v1.2.3", "from_kafka_1m/neo")
            report_path = next(root.rglob("*.json"))

            report = load_report(report_path, artifact_id="v1.2.3")

        assert report is not None
        self.assertEqual(report.pipeline, "from_kafka_1m/neo")
        self.assertEqual(report.artifact_id, "v1.2.3")
