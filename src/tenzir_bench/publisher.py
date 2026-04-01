"""Publishing of benchmark results to S3."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from mypy_boto3_s3.client import S3Client

from .references import (
    ReportIdentity,
    normalize_reports_by_identity,
    parse_destination,
    reference_report_key,
)
from .reports import Report, load_reports, select_fastest

DEFAULT_BUCKET = "tenzir-bench-reports-dev"

_LOG = logging.getLogger(__name__)


class Publisher:
    def __init__(self, bucket: str = DEFAULT_BUCKET) -> None:
        self.bucket: str = bucket
        self.s3: S3Client = boto3.client("s3")

    def publish(self, directory: Path, destination: str, force: bool = False) -> None:
        reports = select_fastest(load_reports(directory))
        self.publish_reports(reports, destination, force=force)

    def publish_reports(
        self,
        reports: Mapping[str, Report] | Mapping[ReportIdentity, Report],
        destination: str,
        *,
        force: bool = False,
    ) -> None:
        resolved = parse_destination(destination, default_bucket=self.bucket)
        normalized = normalize_reports_by_identity(reports)
        for report in normalized.values():
            key = reference_report_key(report, prefix=resolved.prefix)
            if not force and self._exists(resolved.bucket, key):
                continue
            _LOG.info("Uploading %s to s3://%s/%s", report.path, resolved.bucket, key)
            self.s3.upload_file(str(report.path), resolved.bucket, key)

    def _exists(self, bucket: str, key: str) -> bool:
        try:
            _ = self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:  # type: ignore[assignment]
            error = exc.response.get("Error")
            code = error.get("Code") if error is not None else None
            if code == "404":
                return False
            raise
