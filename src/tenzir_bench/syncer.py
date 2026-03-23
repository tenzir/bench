"""Synchronise benchmark artifacts from remote storage."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import boto3

from .metadata import GitHubMetadata
from .paths import BenchPaths
from .reports import matches_identifier

_LOG = logging.getLogger(__name__)

DEFAULT_BUCKET = "tenzir-bench-reports-dev"


class ResultSyncer:
    def __init__(self, paths: BenchPaths, bucket: str = DEFAULT_BUCKET) -> None:
        self.paths = paths
        self.bucket = bucket
        self.s3 = boto3.client("s3")

    def sync_results(self, full: bool = False, artifact_filters: set[str] | None = None) -> None:
        prefix = ""
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/"):
                    continue
                if not full and artifact_filters and not _matches_any_artifact(key, artifact_filters):
                    continue
                local_path = self.paths.results_cache_dir / key
                if local_path.exists():
                    continue
                local_path.parent.mkdir(parents=True, exist_ok=True)
                _LOG.info("Downloading %s", key)
                self.s3.download_file(self.bucket, key, str(local_path))


def sync(paths: BenchPaths, full: bool, refresh: bool) -> None:
    metadata = GitHubMetadata(paths.metadata_cache_dir)
    releases = metadata.fetch_releases(refresh=refresh)
    commits = metadata.fetch_main_commits(refresh=refresh)
    artifact_filters = None if full else _artifact_filters(releases, commits)
    if not full and not artifact_filters:
        _LOG.warning("No release or main metadata available; nothing to sync")
        return
    syncer = ResultSyncer(paths)
    syncer.sync_results(
        full=full,
        artifact_filters=artifact_filters,
    )


def _artifact_filters(
    releases: list[dict[str, object]],
    commits: list[dict[str, object]],
) -> set[str]:
    filters = {
        tag
        for release in releases
        if isinstance((tag := release.get("tag")), str) and tag
    }
    if commits:
        sha = commits[0].get("sha")
        if isinstance(sha, str) and sha:
            filters.add(sha)
    return filters


def _matches_any_artifact(key: str, artifact_filters: set[str]) -> bool:
    artifact_id = _artifact_id_from_key(key)
    return any(matches_identifier(artifact_id, expected) for expected in artifact_filters)


def _artifact_id_from_key(key: str) -> str | None:
    parts = PurePosixPath(key).parts
    if len(parts) < 5:
        return None
    return parts[-3]
