"""Minimal S3 typing helpers without runtime dependency on boto3 stubs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client as _S3Client
    from mypy_boto3_s3.paginator import ListObjectsV2Paginator as _ListObjectsV2Paginator
    from mypy_boto3_s3.type_defs import (
        ListObjectsV2OutputTypeDef as _ListObjectsV2OutputTypeDef,
        ObjectTypeDef as _ObjectTypeDef,
    )

    S3Client = _S3Client
    ListObjectsV2Paginator = _ListObjectsV2Paginator
    ListObjectsV2OutputTypeDef = _ListObjectsV2OutputTypeDef
    ObjectTypeDef = _ObjectTypeDef
else:

    class ObjectTypeDef(TypedDict, total=False):
        Key: str

    class ListObjectsV2OutputTypeDef(TypedDict, total=False):
        Contents: list[ObjectTypeDef]

    class ListObjectsV2Paginator(Protocol):
        def paginate(self, **kwargs: object) -> list[ListObjectsV2OutputTypeDef]: ...

    class S3Client(Protocol):
        def upload_file(self, filename: str, bucket: str, key: str) -> object: ...

        def head_object(self, **kwargs: object) -> object: ...

        def get_paginator(self, operation_name: str) -> ListObjectsV2Paginator: ...

        def download_file(self, bucket: str, key: str, filename: str) -> object: ...


__all__ = [
    "ListObjectsV2OutputTypeDef",
    "ListObjectsV2Paginator",
    "ObjectTypeDef",
    "S3Client",
]
