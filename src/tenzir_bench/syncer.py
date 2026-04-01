"""Synchronise benchmark artifacts from remote storage."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

import boto3

from .metadata import GitHubMetadata, MainCommitMetadata, ReleaseMetadata
from .paths import BenchPaths
from .s3_types import ListObjectsV2OutputTypeDef, ListObjectsV2Paginator, ObjectTypeDef, S3Client

_LOG = logging.getLogger(__name__)

DEFAULT_BUCKET = "tenzir-bench-reports-dev"


class ResultSyncer:
    def __init__(self, paths: BenchPaths, bucket: str = DEFAULT_BUCKET) -> None:
        self.paths: BenchPaths = paths
        self.bucket: str = bucket
        self.s3: S3Client = boto3.client("s3")

    def sync_results(
        self,
        *,
        full: bool = False,
        reference_prefixes: set[PurePosixPath] | None = None,
    ) -> None:
        if full:
            prefixes: Iterable[PurePosixPath] = (PurePosixPath(""),)
        else:
            prefixes = sorted(reference_prefixes or {PurePosixPath("")})
        paginator: ListObjectsV2Paginator = self.s3.get_paginator("list_objects_v2")
        for prefix in prefixes:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=str(prefix)):
                typed_page: ListObjectsV2OutputTypeDef = page
                for obj in typed_page.get("Contents", []):
                    typed_obj: ObjectTypeDef = obj
                    key = typed_obj.get("Key")
                    if not isinstance(key, str):
                        continue
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
    releases = metadata.fetch_releases(refresh=refresh)
    commits = metadata.fetch_main_commits(refresh=refresh)
    reference_prefixes = None if full else _reference_prefixes(releases, commits)
    if not full and not reference_prefixes:
        _LOG.warning("No release or main metadata available; nothing to sync")
        return
    syncer = ResultSyncer(paths)
    syncer.sync_results(
        full=full,
        reference_prefixes=reference_prefixes,
    )


def _reference_prefixes(
    releases: Sequence[ReleaseMetadata],
    commits: Sequence[MainCommitMetadata],
) -> set[PurePosixPath]:
    prefixes = {
        PurePosixPath("refs") / "tags" / tag for release in releases if (tag := release["tag"])
    }
    if commits:
        sha = commits[0]["sha"]
        if sha:
            prefixes.add(PurePosixPath("refs") / "main" / sha)
    return prefixes
