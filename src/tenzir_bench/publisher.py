"""Publishing of benchmark results to S3."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

DEFAULT_BUCKET = "tenzir-bench-reports-dev"

_LOG = logging.getLogger(__name__)


class Publisher:
    def __init__(self, bucket: str = DEFAULT_BUCKET) -> None:
        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def publish(self, directory: Path, destination: str, force: bool = False) -> None:
        bucket, prefix = _parse_destination(destination, default_bucket=self.bucket)
        files = _list_json(directory)
        for file in files:
            key = str(Path(prefix) / file)
            if not force and self._exists(bucket, key):
                continue
            full_path = directory / file
            _LOG.info("Uploading %s to s3://%s/%s", full_path, bucket, key)
            self.s3.upload_file(str(full_path), bucket, key)

    def _exists(self, bucket: str, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:  # type: ignore[assignment]
            if exc.response["Error"]["Code"] == "404":
                return False
            raise


def _parse_destination(destination: str, default_bucket: str) -> tuple[str, Path]:
    parsed = urlparse(destination)
    if parsed.scheme == "s3":
        bucket = parsed.netloc or default_bucket
        prefix = Path(parsed.path.lstrip("/"))
    else:
        bucket = default_bucket
        prefix = Path(destination)
    return bucket, prefix


def _list_json(directory: Path) -> Iterable[Path]:
    files: list[Path] = []
    if not directory.exists():
        return files
    for file in directory.rglob("*.json"):
        try:
            files.append(file.relative_to(directory))
        except ValueError:
            continue
    return sorted(files)
