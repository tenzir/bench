"""Utilities for resolving cache and state directories for the benchmark CLI."""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from platformdirs import PlatformDirs


@dataclass(frozen=True)
class BenchPaths:
    """Resolve cache, state, and config directories with XDG compatibility."""

    dirs: PlatformDirs
    ensure_dir: Callable[[Path], Path] = staticmethod(lambda path: path)
    cache_root: Path = field(default_factory=Path)
    state_root: Path = field(default_factory=Path)

    @classmethod
    def create(cls) -> "BenchPaths":
        dirs = PlatformDirs(appname="tenzir-bench", appauthor="Tenzir", ensure_exists=True)
        cache_root = _ensure_with_fallback(Path(dirs.user_cache_path), fallback="cache")
        state_root = _ensure_with_fallback(Path(dirs.user_state_path), fallback="state")

        def ensure(path: Path) -> Path:
            return _ensure(path)

        return cls(dirs=dirs, ensure_dir=ensure, cache_root=cache_root, state_root=state_root)

    @property
    def cache_dir(self) -> Path:
        return self.cache_root

    @property
    def state_dir(self) -> Path:
        return self.state_root

    @property
    def datasets_cache_dir(self) -> Path:
        return self.ensure_dir(self.cache_dir / "datasets")

    @property
    def results_cache_dir(self) -> Path:
        return self.ensure_dir(self.cache_dir / "results")

    @property
    def metadata_cache_dir(self) -> Path:
        return self.ensure_dir(self.cache_dir / "metadata")

    @property
    def results_state_dir(self) -> Path:
        return self.ensure_dir(self.state_dir / "results")


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_with_fallback(preferred: Path, fallback: str) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        if os.access(preferred, os.W_OK | os.X_OK):
            return preferred
    except OSError as exc:
        if exc.errno not in (errno.EROFS, errno.EPERM):
            raise
    local = Path.cwd() / ".bench" / fallback
    local.mkdir(parents=True, exist_ok=True)
    return local
