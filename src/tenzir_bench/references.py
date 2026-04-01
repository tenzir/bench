"""Semantic storage helpers for published benchmark references."""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeAlias
from urllib.parse import urlparse

import boto3

from .reports import Report, load_report, parse_report_payload


@dataclass(frozen=True)
class ReferenceDestination:
    bucket: str
    prefix: PurePosixPath


ReportIdentity: TypeAlias = tuple[str, str]


def parse_destination(destination: str, *, default_bucket: str) -> ReferenceDestination:
    parsed = urlparse(destination)
    if parsed.scheme == "s3":
        bucket = parsed.netloc or default_bucket
        prefix = PurePosixPath(parsed.path.lstrip("/"))
    else:
        bucket = default_bucket
        prefix = PurePosixPath(destination)
    return ReferenceDestination(bucket=bucket, prefix=prefix)


def report_identity(report: Report) -> ReportIdentity:
    if not report.benchmark_id:
        raise RuntimeError(f"{report.path}: missing benchmark_id in benchmark report")
    if not report.implementation_id:
        raise RuntimeError(f"{report.path}: missing implementation_id in benchmark report")
    return report.benchmark_id, report.implementation_id


def normalize_reports_by_identity(
    reports: Mapping[str, Report] | Mapping[ReportIdentity, Report],
) -> dict[ReportIdentity, Report]:
    normalized: dict[ReportIdentity, Report] = {}
    for report in reports.values():
        normalized[report_identity(report)] = report
    return normalized


def reference_report_key(report: Report, *, prefix: PurePosixPath) -> str:
    if not report.target:
        raise RuntimeError(f"{report.path}: missing target in benchmark report")
    if not report.hardware_key:
        raise RuntimeError(f"{report.path}: missing hardware.key in benchmark report")
    benchmark_id, implementation_id = report_identity(report)
    return str(prefix / report.hardware_key / benchmark_id / implementation_id / "report.json")


def load_reference_reports(
    directory: Path,
    *,
    benchmarks: Collection[str] | None = None,
    hardware_key: str | None = None,
    target: str | None = None,
) -> dict[ReportIdentity, Report]:
    selected = set(benchmarks or ())
    reports: dict[ReportIdentity, Report] = {}
    if not directory.exists():
        return reports
    for file in directory.rglob("*.json"):
        report = load_report(file)
        if report is None:
            continue
        benchmark_id, implementation_id = report_identity(report)
        if selected and benchmark_id not in selected:
            continue
        if hardware_key is not None and report.hardware_key != hardware_key:
            continue
        if target is not None and report.target != target:
            continue
        reports[(benchmark_id, implementation_id)] = report
    return reports


def download_reference_reports(
    destination: str,
    *,
    benchmarks: Collection[str] | None = None,
    hardware_key: str | None = None,
    target: str | None = None,
    default_bucket: str = "",
) -> dict[ReportIdentity, Report]:
    resolved = parse_destination(destination, default_bucket=default_bucket)
    selected = set(benchmarks or ())
    reports: dict[ReportIdentity, Report] = {}
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=resolved.bucket, Prefix=str(resolved.prefix)):
        for obj in page.get("Contents", []):
            key = obj.get("Key")
            if not isinstance(key, str) or not key.endswith(".json"):
                continue
            body = s3.get_object(Bucket=resolved.bucket, Key=key)["Body"].read().decode("utf-8")
            report = parse_report_payload(
                json.loads(body),
                path=Path(f"s3://{resolved.bucket}/{key}"),
            )
            if report is None:
                continue
            benchmark_id, implementation_id = report_identity(report)
            if selected and benchmark_id not in selected:
                continue
            if hardware_key is not None and report.hardware_key != hardware_key:
                continue
            if target is not None and report.target != target:
                continue
            reports[(benchmark_id, implementation_id)] = report
    return reports


def missing_report_identities(
    expected: Collection[ReportIdentity],
    available: Mapping[ReportIdentity, Report] | Mapping[str, Report],
) -> set[ReportIdentity]:
    available_identities = set(normalize_reports_by_identity(available))
    return {identity for identity in expected if identity not in available_identities}
