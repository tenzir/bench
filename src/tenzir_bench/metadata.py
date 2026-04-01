"""GitHub metadata caching utilities."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

from github import Github

_LOG = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 30 * 60
REPOSITORY = "tenzir/tenzir"
_T = TypeVar("_T")


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
        self.releases_cache: MetadataCache[list[dict[str, Any]]] = MetadataCache(
            cache_dir / "github_releases.json", ttl_seconds
        )
        self.main_cache: MetadataCache[list[dict[str, Any]]] = MetadataCache(
            cache_dir / "github_main.json", ttl_seconds
        )

    def fetch_releases(self, refresh: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        cached = self.releases_cache.load(refresh=refresh)
        if cached is not None:
            return cached
        _LOG.info("Fetching release metadata from GitHub")
        repo = self.client.get_repo(REPOSITORY)
        releases = []
        for index, release in enumerate(repo.get_releases()):
            if index >= limit:
                break
            releases.append(
                {
                    "tag": release.tag_name,
                    "published_at": release.published_at.isoformat()
                    if release.published_at
                    else None,
                    "target": release.target_commitish,
                },
            )
        self.releases_cache.save(releases)
        return releases

    def fetch_main_commits(self, refresh: bool = False, limit: int = 20) -> list[dict[str, Any]]:
        cached = self.main_cache.load(refresh=refresh)
        if cached is not None:
            return cached
        _LOG.info("Fetching main branch metadata from GitHub")
        repo = self.client.get_repo(REPOSITORY)
        commits = []
        for index, commit in enumerate(repo.get_commits(sha="main")):
            if index >= limit:
                break
            commits.append(
                {
                    "sha": commit.sha,
                    "date": commit.commit.author.date.isoformat()
                    if commit.commit.author and commit.commit.author.date
                    else None,
                },
            )
        self.main_cache.save(commits)
        return commits
