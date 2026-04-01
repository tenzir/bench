"""GitHub metadata caching utilities."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Generic, TypeVar, TypedDict, cast

from github import Github

_LOG = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30 * 60
REPOSITORY = "tenzir/tenzir"
_T = TypeVar("_T")


class ReleaseMetadata(TypedDict):
    tag: str
    published_at: str | None
    target: str | None


class MainCommitMetadata(TypedDict):
    sha: str
    date: str | None


@dataclass
class MetadataCache(Generic[_T]):
    path: Path
    ttl_seconds: int = DEFAULT_TTL_SECONDS

    def load(self, refresh: bool = False) -> _T | None:
        if not self.path.exists() or refresh:
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        timestamp = payload.get("fetched_at")
        if not timestamp:
            return None
        fetched_at = datetime.fromisoformat(timestamp)
        if datetime.now(UTC) - fetched_at > timedelta(seconds=self.ttl_seconds):
            return None
        return cast(_T | None, payload.get("data"))

    def save(self, data: _T) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "data": data,
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")


class GitHubMetadata:
    def __init__(self, cache_dir: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.cache_dir = cache_dir
        token = os.getenv("GITHUB_TOKEN")
        self.client = Github(login_or_token=token) if token else Github()
        self.releases_cache: MetadataCache[list[ReleaseMetadata]] = MetadataCache(
            cache_dir / "github_releases.json", ttl_seconds
        )
        self.main_cache: MetadataCache[list[MainCommitMetadata]] = MetadataCache(
            cache_dir / "github_main.json", ttl_seconds
        )

    def fetch_releases(self, refresh: bool = False, limit: int = 20) -> list[ReleaseMetadata]:
        cached = self.releases_cache.load(refresh=refresh)
        if cached is not None:
            return cached
        _LOG.info("Fetching release metadata from GitHub")
        repo = self.client.get_repo(REPOSITORY)
        releases: list[ReleaseMetadata] = []
        for index, release in enumerate(repo.get_releases()):
            if index >= limit:
                break
            tag_name = getattr(release, "tag_name", None)
            if not isinstance(tag_name, str) or not tag_name:
                continue
            published_at = _isoformat_or_none(getattr(release, "published_at", None))
            target_commitish = getattr(release, "target_commitish", None)
            releases.append(
                {
                    "tag": tag_name,
                    "published_at": published_at,
                    "target": target_commitish if isinstance(target_commitish, str) else None,
                },
            )
        self.releases_cache.save(releases)
        return releases

    def fetch_main_commits(
        self, refresh: bool = False, limit: int = 20
    ) -> list[MainCommitMetadata]:
        cached = self.main_cache.load(refresh=refresh)
        if cached is not None:
            return cached
        _LOG.info("Fetching main branch metadata from GitHub")
        repo = self.client.get_repo(REPOSITORY)
        commits: list[MainCommitMetadata] = []
        for index, commit in enumerate(repo.get_commits(sha="main")):
            if index >= limit:
                break
            sha = getattr(commit, "sha", None)
            if not isinstance(sha, str) or not sha:
                continue
            commit_obj = getattr(commit, "commit", None)
            author = getattr(commit_obj, "author", None)
            date = _isoformat_or_none(getattr(author, "date", None))
            commits.append(
                {
                    "sha": sha,
                    "date": date,
                },
            )
        self.main_cache.save(commits)
        return commits


def _isoformat_or_none(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None
