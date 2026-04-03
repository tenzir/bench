import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tenzir_bench.references import (
    download_reference_reports,
    load_reference_reports,
    missing_report_identities,
    normalize_reports_by_identity,
    parse_destination,
    reference_report_key,
)
from tenzir_bench.reports import Report


def _write_reference_report(
    root: Path,
    *,
    ref_kind: str,
    ref_value: str,
    target: str,
    hardware_key: str,
    benchmark_id: str,
    implementation_id: str,
) -> Path:
    path = (
        root
        / "refs"
        / ref_kind
        / ref_value
        / target
        / hardware_key
        / benchmark_id
        / implementation_id
        / "report.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "pipeline": f"{benchmark_id}/{implementation_id}",
                "benchmark_id": benchmark_id,
                "implementation_id": implementation_id,
                "target": target,
                "hardware": {"key": hardware_key},
                "build": {"version": ref_value},
                "runtime": {
                    "wall_clock": 1.0,
                    "max_resident_set_kb": 1024,
                },
            },
        ),
        encoding="utf-8",
    )
    return path


class ReferencesTest(unittest.TestCase):
    def test_parse_destination_supports_s3_and_prefix_only(self) -> None:
        resolved = parse_destination(
            "s3://bucket/runs/refs/main/abc/static", default_bucket="default"
        )
        self.assertEqual(resolved.bucket, "bucket")
        self.assertEqual(str(resolved.prefix), "runs/refs/main/abc/static")

        resolved = parse_destination("refs/main/abc/static", default_bucket="default")
        self.assertEqual(resolved.bucket, "default")
        self.assertEqual(str(resolved.prefix), "refs/main/abc/static")

    def test_reference_report_key_uses_hardware_benchmark_and_implementation(self) -> None:
        report = Report(
            path=Path("/tmp/report.json"),
            pipeline="from_kafka_route53/neo-string",
            benchmark_id="from_kafka_route53",
            implementation_id="neo-string",
            target="docker",
            hardware_key="ubuntu-latest_x86_64_unknown_4c",
            wall_clock=1.0,
            rss_kb=1024,
            build_version="v1.2.3",
            artifact_id=None,
        )

        key = reference_report_key(report, prefix=Path("refs/main/abc123/docker"))

        self.assertEqual(
            key,
            "refs/main/abc123/docker/ubuntu-latest_x86_64_unknown_4c/"
            "from_kafka_route53/neo-string/report.json",
        )

    def test_load_reference_reports_filters_by_benchmark_hardware_and_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_reference_report(
                root,
                ref_kind="main",
                ref_value="abc123",
                target="static",
                hardware_key="runner-a",
                benchmark_id="from_kafka_route53",
                implementation_id="neo-string",
            )
            _write_reference_report(
                root,
                ref_kind="main",
                ref_value="abc123",
                target="docker",
                hardware_key="runner-a",
                benchmark_id="from_file_route53_ocsf",
                implementation_id="neo",
            )
            _write_reference_report(
                root,
                ref_kind="main",
                ref_value="abc123",
                target="static",
                hardware_key="runner-b",
                benchmark_id="from_kafka_route53",
                implementation_id="legacy-string",
            )

            reports = load_reference_reports(
                root / "refs" / "main" / "abc123",
                benchmarks={"from_kafka_route53"},
                hardware_key="runner-a",
                target="static",
            )

        self.assertEqual(set(reports), {("from_kafka_route53", "neo-string")})

    def test_normalize_reports_by_identity_uses_benchmark_and_implementation(self) -> None:
        report = Report(
            path=Path("/tmp/report.json"),
            pipeline="from_kafka_route53/neo-string",
            benchmark_id="from_kafka_route53",
            implementation_id="neo-string",
            target="docker",
            hardware_key="runner-a",
            wall_clock=1.0,
            rss_kb=1024,
            build_version="v1.2.3",
            artifact_id=None,
        )

        normalized = normalize_reports_by_identity({"from_kafka_route53/neo-string": report})

        self.assertEqual(set(normalized), {("from_kafka_route53", "neo-string")})

    def test_missing_report_identities_compares_by_benchmark_and_implementation(self) -> None:
        report = Report(
            path=Path("/tmp/report.json"),
            pipeline="from_kafka_route53/neo-string",
            benchmark_id="from_kafka_route53",
            implementation_id="neo-string",
            target="docker",
            hardware_key="runner-a",
            wall_clock=1.0,
            rss_kb=1024,
            build_version="v1.2.3",
            artifact_id=None,
        )

        missing = missing_report_identities(
            {
                ("from_kafka_route53", "neo-string"),
                ("from_kafka_route53", "legacy-string"),
            },
            {"from_kafka_route53/neo-string": report},
        )

        self.assertEqual(missing, {("from_kafka_route53", "legacy-string")})

    def test_download_reference_reports_filters_remote_reports(self) -> None:
        key = "refs/main/abc123/static/runner-a/from_kafka_route53/neo-string/report.json"
        payload = json.dumps(
            {
                "schema_version": 2,
                "pipeline": "from_kafka_route53/neo-string",
                "benchmark_id": "from_kafka_route53",
                "implementation_id": "neo-string",
                "target": "static",
                "hardware": {"key": "runner-a"},
                "build": {"version": "abc123"},
                "runtime": {
                    "wall_clock": 1.0,
                    "max_resident_set_kb": 1024,
                },
            },
        )

        class _Body:
            def read(self) -> bytes:
                return payload.encode("utf-8")

        class _Paginator:
            def paginate(self, *, Bucket: str, Prefix: str):
                self.bucket = Bucket
                self.prefix = Prefix
                return [{"Contents": [{"Key": key}]}]

        class _S3:
            def __init__(self) -> None:
                self.paginator = _Paginator()

            def get_paginator(self, name: str):
                self.name = name
                return self.paginator

            def get_object(self, *, Bucket: str, Key: str):
                self.bucket = Bucket
                self.key = Key
                return {"Body": _Body()}

        s3 = _S3()

        with patch("tenzir_bench.references.create_s3_client", return_value=s3):
            reports = download_reference_reports(
                "s3://bucket/refs/main/abc123/static",
                benchmarks={"from_kafka_route53"},
                hardware_key="runner-a",
                target="static",
            )

        self.assertEqual(set(reports), {("from_kafka_route53", "neo-string")})
