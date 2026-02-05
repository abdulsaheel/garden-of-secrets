"""S3-compatible object storage service.

Works with AWS S3, MinIO, Cloudflare R2, and any S3-compatible endpoint.
"""

import hashlib
import uuid
from io import BytesIO
from typing import Optional

import aioboto3
import aiohttp
from botocore.config import Config as BotoConfig

from app.config import get_settings

settings = get_settings()


def _get_session():
    return aioboto3.Session(
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def _client_kwargs():
    kwargs = {
        "endpoint_url": settings.s3_endpoint_url,
        "config": BotoConfig(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=30,
            read_timeout=60,
        ),
    }
    if not settings.s3_use_ssl:
        kwargs["use_ssl"] = False
    return kwargs


def _prefixed(key: str) -> str:
    if settings.s3_prefix:
        return f"{settings.s3_prefix.rstrip('/')}/{key}"
    return key


def _unprefix(key: str) -> str:
    if settings.s3_prefix:
        prefix = settings.s3_prefix.rstrip("/") + "/"
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


class S3Service:
    """Async S3-compatible storage operations."""

    @staticmethod
    async def ensure_bucket():
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            try:
                await client.head_bucket(Bucket=settings.s3_bucket)
            except Exception:
                await client.create_bucket(Bucket=settings.s3_bucket)

    @staticmethod
    async def put_object(key: str, content: bytes, content_type: str = "application/octet-stream") -> str:
        """Upload content and return the S3 key."""
        full_key = _prefixed(key)
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            await client.put_object(
                Bucket=settings.s3_bucket,
                Key=full_key,
                Body=content,
                ContentType=content_type,
            )
        return full_key

    @staticmethod
    async def get_object(key: str) -> bytes:
        """Download and return object content."""
        full_key = _prefixed(key)
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            resp = await client.get_object(Bucket=settings.s3_bucket, Key=full_key)
            data = await resp["Body"].read()
        return data

    @staticmethod
    async def delete_object(key: str):
        full_key = _prefixed(key)
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            await client.delete_object(Bucket=settings.s3_bucket, Key=full_key)

    @staticmethod
    async def delete_objects(keys: list[str]):
        if not keys:
            return
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            objects = [{"Key": _prefixed(k)} for k in keys]
            # S3 delete_objects supports max 1000 keys per request
            for i in range(0, len(objects), 1000):
                batch = objects[i:i + 1000]
                await client.delete_objects(
                    Bucket=settings.s3_bucket,
                    Delete={"Objects": batch, "Quiet": True},
                )

    @staticmethod
    async def copy_object(src_key: str, dst_key: str):
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            await client.copy_object(
                Bucket=settings.s3_bucket,
                CopySource={"Bucket": settings.s3_bucket, "Key": _prefixed(src_key)},
                Key=_prefixed(dst_key),
            )

    @staticmethod
    async def list_objects(prefix: str = "", delimiter: str = "") -> dict:
        """List objects with optional prefix and delimiter.

        Returns dict with 'files' (list of object info) and 'folders' (list of common prefixes).
        """
        full_prefix = _prefixed(prefix)
        session = _get_session()
        files = []
        folders = []
        async with session.client("s3", **_client_kwargs()) as client:
            paginator = client.get_paginator("list_objects_v2")
            params = {"Bucket": settings.s3_bucket, "Prefix": full_prefix}
            if delimiter:
                params["Delimiter"] = delimiter
            async for page in paginator.paginate(**params):
                for obj in page.get("Contents", []):
                    files.append({
                        "key": _unprefix(obj["Key"]),
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"],
                    })
                for cp in page.get("CommonPrefixes", []):
                    folders.append(_unprefix(cp["Prefix"]))
        return {"files": files, "folders": folders}

    @staticmethod
    async def head_object(key: str) -> Optional[dict]:
        full_key = _prefixed(key)
        session = _get_session()
        async with session.client("s3", **_client_kwargs()) as client:
            try:
                resp = await client.head_object(Bucket=settings.s3_bucket, Key=full_key)
                return {
                    "size": resp["ContentLength"],
                    "content_type": resp.get("ContentType", ""),
                    "last_modified": resp["LastModified"],
                }
            except Exception:
                return None

    @staticmethod
    def generate_version_key() -> str:
        """Generate a unique S3 key for a version object."""
        return f"_versions/{uuid.uuid4().hex}"

    @staticmethod
    def generate_staging_key() -> str:
        """Generate a unique S3 key for a staged CR file."""
        return f"_staging/{uuid.uuid4().hex}"

    @staticmethod
    def compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def guess_content_type(path: str) -> str:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        mapping = {
            "html": "text/html", "css": "text/css", "js": "application/javascript",
            "json": "application/json", "xml": "application/xml", "yaml": "text/yaml",
            "yml": "text/yaml", "md": "text/markdown", "txt": "text/plain",
            "py": "text/x-python", "rb": "text/x-ruby", "go": "text/x-go",
            "rs": "text/x-rust", "java": "text/x-java", "ts": "text/typescript",
            "tsx": "text/typescript", "jsx": "text/javascript", "sh": "text/x-shellscript",
            "sql": "text/x-sql", "csv": "text/csv", "svg": "image/svg+xml",
            "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "gif": "image/gif", "pdf": "application/pdf", "zip": "application/zip",
            "tar": "application/x-tar", "gz": "application/gzip",
        }
        return mapping.get(ext, "application/octet-stream")

    @staticmethod
    def is_text_file(path: str) -> bool:
        ct = S3Service.guess_content_type(path)
        return ct.startswith("text/") or ct in (
            "application/json", "application/javascript", "application/xml",
            "application/x-yaml", "text/markdown", "text/x-python",
        )
