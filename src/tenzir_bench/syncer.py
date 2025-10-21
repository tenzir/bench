"""Synchronise benchmark artifacts from remote storage."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import boto3

from .metadata import GitHubMetadata
from .paths import BenchPaths

_LOG = logging.getLogger(__name__)

DEFAULT_BUCKET = "tenzir-bench-reports-dev"


class ResultSyncer:
    def __init__(self, paths: BenchPaths, bucket: str = DEFAULT_BUCKET) -> None:
        self.paths = paths
        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def sync_results(self, full: bool = False) -> None:
        prefix = ""  # Placeholder: architecture-specific prefix could be added here
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                local_path = self.paths.results_cache_dir / key
                if local_path.exists():
                    continue
                local_path.parent.mkdir(parents=True, exist_ok=True)
                _LOG.info("Downloading %s", key)
                self.s3.download_file(self.bucket, key, str(local_path))


def sync(paths: BenchPaths, full: bool, refresh: bool) -> None:
    metadata = GitHubMetadata(paths.metadata_cache_dir)
    metadata.fetch_releases(refresh=refresh)
    metadata.fetch_main_commits(refresh=refresh)
    syncer = ResultSyncer(paths)
    syncer.sync_results(full=full)
