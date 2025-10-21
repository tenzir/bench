"""Hash helpers for benchmark definitions and inputs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from .definitions import BenchmarkDefinition

_BUFFER_SIZE = 1024 * 1024


def hash_benchmark(defn: BenchmarkDefinition) -> str:
    hasher = hashlib.sha256()
    hasher.update(defn.pipeline_body.encode("utf-8"))
    for key, value in sorted(defn.env.items()):
        hasher.update(b"env\0" + key.encode("utf-8") + b"\0" + value.encode("utf-8"))
    for arg in defn.tenzir_args:
        hasher.update(b"arg\0" + arg.encode("utf-8"))
    hasher.update(b"runner\0" + defn.runner.encode("utf-8"))
    return hasher.hexdigest()


def hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_BUFFER_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def hash_inputs(paths: Iterable[Path]) -> str:
    hasher = hashlib.sha256()
    for path in sorted(paths):
        hasher.update(hash_file(path).encode("utf-8"))
    return hasher.hexdigest()
