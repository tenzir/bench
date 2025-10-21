"""Utilities for resolving cache and state directories for the benchmark CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from platformdirs import PlatformDirs


@dataclass(frozen=True)
class BenchPaths:
    """Resolve cache, state, and config directories with XDG compatibility."""

    dirs: PlatformDirs
    ensure_dir: Callable[[Path], Path] = staticmethod(lambda path: path)

    @classmethod
    def create(cls) -> "BenchPaths":
        dirs = PlatformDirs(appname="tenzir-bench", appauthor="Tenzir", ensure_exists=True)
        return cls(dirs=dirs, ensure_dir=lambda path: _ensure(path))

    @property
    def cache_dir(self) -> Path:
        return Path(self.dirs.user_cache_path)

    @property
    def state_dir(self) -> Path:
        return Path(self.dirs.user_state_path)

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
