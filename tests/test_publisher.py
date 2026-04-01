import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError

from tenzir_bench.publisher import Publisher
from tenzir_bench.reports import Report


class PublisherTest(unittest.TestCase):
    def test_publish_reports_uses_semantic_reference_key(self) -> None:
        report = Report(
            path=Path("/tmp/report.json"),
            pipeline="from_kafka_route53/neo-string",
            benchmark_id="from_kafka_route53",
            implementation_id="neo-string",
            target="static",
            hardware_key="runner-a",
            wall_clock=1.0,
            rss_kb=1024,
            build_version="v1.2.3",
            artifact_id=None,
        )

        class _S3:
            def __init__(self) -> None:
                self.uploads: list[tuple[str, str, str]] = []

            def head_object(self, *, Bucket: str, Key: str) -> None:
                raise Exception("unexpected")

            def upload_file(self, filename: str, bucket: str, key: str) -> None:
                self.uploads.append((filename, bucket, key))

        s3 = _S3()

        def _head_object(*, Bucket: str, Key: str) -> None:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

        s3.head_object = _head_object  # type: ignore[method-assign]

        with patch("tenzir_bench.publisher.boto3.client", return_value=s3):
            publisher = Publisher(bucket="bucket")
            publisher.publish_reports(
                {("from_kafka_route53", "neo-string"): report},
                "refs/main/abc123/static",
            )

        self.assertEqual(
            s3.uploads,
            [
                (
                    "/tmp/report.json",
                    "bucket",
                    "refs/main/abc123/static/runner-a/from_kafka_route53/neo-string/report.json",
                ),
            ],
        )
