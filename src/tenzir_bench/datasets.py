"""Dataset preparation utilities."""

from __future__ import annotations

import csv
import json
import logging
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

from .paths import BenchPaths

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    url: Union[str, Path]
    download: Path
    final: Path
    post: Optional[Callable[[Path, Path, BenchPaths, bool], None]] = None


class DatasetManager:
    def __init__(self, paths: BenchPaths) -> None:
        self.paths = paths

    def prepare(self, force: bool = False) -> None:
        for spec in self._specs():
            self._prepare_single(spec, force=force)

    # ------------------------------------------------------------------
    def _prepare_single(self, spec: DatasetSpec, force: bool) -> None:
        download_path = self.paths.datasets_cache_dir / spec.download
        final_path = self.paths.datasets_cache_dir / spec.final
        if not download_path.exists() or force:
            _LOG.info("Fetching dataset %s", spec.name)
            self._download(spec.url, download_path, force=force)
        else:
            _LOG.info("Dataset %s already downloaded", spec.name)
        if spec.post:
            spec.post(download_path, final_path, self.paths, force)
        elif download_path != final_path:
            _ensure_parent(final_path)
            if force or not final_path.exists():
                shutil.copy2(download_path, final_path)

    def _download(self, url: Union[str, Path], destination: Path, force: bool) -> None:
        _ensure_parent(destination)
        if destination.exists() and not force:
            return
        if isinstance(url, Path) or (isinstance(url, str) and url.startswith("file://")):
            source = Path(url[7:]) if isinstance(url, str) else url
            shutil.copy2(source, destination)
            return
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(url) as response, temp_path.open("wb") as handle:  # type: ignore[arg-type]
                shutil.copyfileobj(response, handle)
            temp_path.replace(destination)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    # ------------------------------------------------------------------
    def _specs(self) -> Iterable[DatasetSpec]:
        root = Path("datasets")
        return (
            DatasetSpec(
                name="suricata-eve",
                url="https://datasets.tenzir.tools/M57/suricata.json.zst",
                download=root / "suricata" / "eve.json.zst",
                final=root / "suricata" / "eve.json",
                post=_suricata_post,
            ),
            DatasetSpec(
                name="zeek-all",
                url="https://datasets.tenzir.tools/M57/zeek-all.log.zst",
                download=root / "zeek" / "zeek-all.log.zst",
                final=root / "zeek" / "zeek-all.log",
                post=_zeek_post,
            ),
        )


# ---------------------------------------------------------------------------
# Post-processing helpers

def _suricata_post(download: Path, final: Path, paths: BenchPaths, force: bool) -> None:
    extracted = _decompress_zst(download, final, force=force)
    kv_log = extracted.parent / "eve.kv.log"
    if kv_log.exists() and not force:
        return
    _LOG.info("Generating %s", kv_log)
    keys = ["timestamp", "event_type", "src_ip", "src_port", "dest_ip", "dest_port", "proto"]
    with extracted.open("r", encoding="utf-8") as source, kv_log.open("w", encoding="utf-8") as sink:
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            fields = [f"{key}={record[key]}" for key in keys if key in record]
            if fields:
                sink.write(" ".join(fields) + "\n")


def _zeek_post(download: Path, final: Path, paths: BenchPaths, force: bool) -> None:
    extracted = _decompress_zst(download, final, force=force)
    conn_log = extracted.parent / "conn.log"
    if not conn_log.exists() or force:
        _extract_conn_log(extracted, conn_log)
    conn_csv = extracted.parent / "conn.csv"
    conn_kv = extracted.parent / "conn.kv.log"
    if conn_csv.exists() and conn_kv.exists() and not force:
        return
    _LOG.info("Generating %s and %s", conn_csv, conn_kv)
    _convert_conn_to_csv(conn_log, conn_csv, conn_kv)


def _decompress_zst(source: Path, destination: Path, force: bool) -> Path:
    if destination.exists() and not force:
        return destination
    zstd = shutil.which("zstd")
    if not zstd:
        raise RuntimeError("zstd binary is required to decompress datasets")
    _ensure_parent(destination)
    subprocess.run([zstd, "-d", "-f", "-o", str(destination), str(source)], check=True)
    return destination


def _extract_conn_log(source: Path, destination: Path) -> None:
    _ensure_parent(destination)
    meta_lines = []
    writing = False
    with source.open("r", encoding="utf-8", errors="ignore") as handle, destination.open(
        "w", encoding="utf-8"
    ) as out:
        for line in handle:
            if line.startswith("#"):
                if line.startswith("#path"):
                    writing = line.split("\t", 1)[1].strip() == "conn"
                    if writing:
                        for meta in meta_lines:
                            out.write(meta)
                        out.write(line)
                    continue
                if not meta_lines:
                    meta_lines.append(line)
                elif writing:
                    out.write(line)
                else:
                    meta_lines.append(line)
                continue
            if writing:
                out.write(line)


def _convert_conn_to_csv(conn_log: Path, csv_path: Path, kv_path: Path) -> None:
    fields = [
        "ts",
        "uid",
        "id.orig_h",
        "id.orig_p",
        "id.resp_h",
        "id.resp_p",
        "proto",
        "service",
        "duration",
        "orig_bytes",
        "resp_bytes",
        "conn_state",
        "history",
        "orig_pkts",
        "resp_pkts",
        "community_id",
    ]
    _ensure_parent(csv_path)
    _ensure_parent(kv_path)
    indices = []
    headers = []
    with (
        conn_log.open("r", encoding="utf-8", errors="ignore") as source,
        csv_path.open("w", newline="", encoding="utf-8") as csv_file,
        kv_path.open("w", encoding="utf-8") as kv_file,
    ):
        writer = csv.writer(csv_file)
        for raw_line in source:
            line = raw_line.rstrip("\n")
            if line.startswith("#fields"):
                file_fields = line.split("\t")[1:]
                indices = [file_fields.index(f) for f in fields if f in file_fields]
                headers = [file_fields[i] for i in indices]
                writer.writerow(headers)
                continue
            if line.startswith("#") or not headers:
                continue
            values = line.split("\t")
            selected = [values[i] if i < len(values) else "" for i in indices]
            writer.writerow(selected)
            kv_pairs = [f"{key}={value}" for key, value in zip(headers, selected) if value and value != "-"]
            if kv_pairs:
                kv_file.write(" ".join(kv_pairs) + "\n")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
