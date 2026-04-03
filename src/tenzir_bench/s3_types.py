"""Minimal S3 typing helpers without runtime dependency on boto3 stubs."""

from __future__ import annotations

from typing import Protocol, TypedDict, cast

import boto3  # pyright: ignore[reportMissingTypeStubs]


class ObjectTypeDef(TypedDict, total=False):
    Key: str


class ListObjectsV2OutputTypeDef(TypedDict, total=False):
    Contents: list[ObjectTypeDef]


class ListObjectsV2Paginator(Protocol):
    def paginate(self, **kwargs: object) -> list[ListObjectsV2OutputTypeDef]: ...


class StreamingBody(Protocol):
    def read(self) -> bytes: ...


class S3Client(Protocol):
    def upload_file(self, filename: str, bucket: str, key: str) -> object: ...

    def head_object(self, **kwargs: object) -> object: ...

    def get_paginator(self, operation_name: str) -> ListObjectsV2Paginator: ...

    def download_file(self, bucket: str, key: str, filename: str) -> object: ...

    def get_object(self, **kwargs: object) -> dict[str, StreamingBody]: ...


def create_s3_client() -> S3Client:
    return cast(S3Client, boto3.client("s3"))  # pyright: ignore[reportUnknownMemberType]


__all__ = [
    "ListObjectsV2OutputTypeDef",
    "ListObjectsV2Paginator",
    "ObjectTypeDef",
    "S3Client",
    "StreamingBody",
    "create_s3_client",
]
