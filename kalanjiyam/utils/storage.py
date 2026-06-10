"""File storage for user uploads (page images, source PDFs, editor images).

All file I/O for uploaded content goes through the :class:`Storage` interface
so that the rest of the application never touches the filesystem directly.
Two backends are supported:

- ``local``: files live under ``UPLOAD_FOLDER`` on the local disk. This is
  the default and matches Kalanjiyam's historical behavior.
- ``s3``: files live in an S3-compatible object store (versitygw, MinIO,
  SeaweedFS, AWS S3, ...). The backend is selected purely by config, so the
  same application image can run against a bundled gateway on-premises or a
  cloud object store.

Keys use the same layout as the historical on-disk tree::

    projects/<project_slug>/pdf/source.pdf
    projects/<project_slug>/pages/<page_slug>.jpg
    projects/<project_slug>/images/<filename>

This means a versitygw POSIX gateway pointed at an existing data directory
serves the historical uploads as-is, with no data migration.
"""

import shutil
import tempfile
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from flask import current_app, redirect, send_file

# Key helpers
# -----------


def project_prefix(project_slug: str) -> str:
    """Key prefix that contains every file belonging to a project."""
    return f"projects/{project_slug}/"


def pdf_key(project_slug: str) -> str:
    """Key of a project's source PDF."""
    return f"projects/{project_slug}/pdf/source.pdf"


def page_image_key(project_slug: str, page_slug: str) -> str:
    """Key of a single page image."""
    return f"projects/{project_slug}/pages/{page_slug}.jpg"


def editor_image_key(project_slug: str, filename: str) -> str:
    """Key of an image uploaded through the rich-text editor."""
    return f"projects/{project_slug}/images/{filename}"


# Storage interface
# -----------------


class Storage(ABC):
    """Abstract interface over a flat key/blob store."""

    @abstractmethod
    def save(self, key: str, source: Path | str | bytes | BinaryIO) -> None:
        """Store `source` (a local path, bytes, or readable file object)."""

    @abstractmethod
    def read_bytes(self, key: str) -> bytes:
        """Return the object's content. Raises ``FileNotFoundError``."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Return True if the object exists."""

    @abstractmethod
    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        """Yield ``(key, size_in_bytes)`` for every object under `prefix`."""

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under `prefix`. Returns the number deleted."""

    @abstractmethod
    def local_copy(self, key: str) -> Path:
        """Return a local filesystem path for the object.

        For the local backend this is the object's real path. For remote
        backends the object is downloaded to a local cache. If the object
        does not exist, the returned path will not exist either; callers
        should check ``.exists()`` as they would for a plain file.
        """

    @abstractmethod
    def serve(self, key: str, **send_file_kwargs):
        """Return a Flask response that serves the object."""

    def total_size(self, prefix: str) -> int:
        """Total size in bytes of all objects under `prefix`."""
        return sum(size for _, size in self.list_keys(prefix))


class LocalStorage(Storage):
    """Stores objects as plain files under a root directory."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        # Guard against path traversal through hostile keys.
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"Key escapes storage root: {key!r}")
        return path

    def save(self, key: str, source: Path | str | bytes | BinaryIO) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(source, (Path, str)):
            shutil.copyfile(source, dest)
        elif isinstance(source, bytes):
            dest.write_bytes(source)
        else:
            with open(dest, "wb") as f:
                shutil.copyfileobj(source, f)

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        base = self._path(prefix)
        if not base.is_dir():
            return
        for path in base.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root)), path.stat().st_size

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix)
        if not base.is_dir():
            return 0
        num_files = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base)
        return num_files

    def local_copy(self, key: str) -> Path:
        return self._path(key)

    def serve(self, key: str, **send_file_kwargs):
        return send_file(self._path(key), **send_file_kwargs)


class S3Storage(Storage):
    """Stores objects in an S3-compatible object store via boto3.

    Works against AWS S3 and any S3-compatible server (versitygw, MinIO,
    SeaweedFS, Ceph RGW) by setting ``endpoint_url``.
    """

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        region: str | None = None,
        public_endpoint_url: str | None = None,
        cache_dir: Path | str | None = None,
    ):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.region = region
        #: If set, page-image responses redirect to presigned URLs on this
        #: endpoint instead of streaming bytes through the app. Requires the
        #: endpoint to be reachable from the user's browser.
        self.public_endpoint_url = public_endpoint_url
        self.cache_dir = Path(
            cache_dir or Path(tempfile.gettempdir()) / "kalanjiyam-storage-cache"
        )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            # Imported lazily so the local backend has no boto3 dependency.
            import boto3
            import botocore.exceptions

            client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name=self.region or "us-east-1",
            )
            # Self-hosted gateways (versitygw, MinIO) start with no buckets;
            # create ours on first use so a fresh deployment works without a
            # manual setup step.
            try:
                client.head_bucket(Bucket=self.bucket)
            except botocore.exceptions.ClientError:
                try:
                    client.create_bucket(Bucket=self.bucket)
                except botocore.exceptions.ClientError:
                    # Creation may be racing another worker, or may be
                    # forbidden (e.g. on AWS with a scoped IAM role, where
                    # the bucket should already exist). Either way, let the
                    # actual operation surface any real error.
                    pass
            self._client = client
        return self._client

    def save(self, key: str, source: Path | str | bytes | BinaryIO) -> None:
        if isinstance(source, (Path, str)):
            self.client.upload_file(str(source), self.bucket, key)
        elif isinstance(source, bytes):
            self.client.put_object(Bucket=self.bucket, Key=key, Body=source)
        else:
            self.client.upload_fileobj(source, self.bucket, key)

    def read_bytes(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(f"s3://{self.bucket}/{key}")
        return response["Body"].read()

    def exists(self, key: str) -> bool:
        import botocore.exceptions

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except botocore.exceptions.ClientError:
            return False

    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["Size"]

    def delete_prefix(self, prefix: str) -> int:
        keys = [key for key, _ in self.list_keys(prefix)]
        for i in range(0, len(keys), 1000):
            batch = keys[i : i + 1000]
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )
        return len(keys)

    def local_copy(self, key: str) -> Path:
        # Uploaded objects are immutable (page images and PDFs are written
        # once; editor images get unique names), so a cached copy never goes
        # stale.
        cached = self.cache_dir / key
        if cached.is_file():
            return cached
        if not self.exists(key):
            return cached
        cached.parent.mkdir(parents=True, exist_ok=True)
        # Download to a temp name and rename so concurrent workers never see
        # a half-written file.
        tmp = cached.with_name(f".{cached.name}.{uuid.uuid4().hex}.tmp")
        try:
            self.client.download_file(self.bucket, key, str(tmp))
            tmp.replace(cached)
        finally:
            tmp.unlink(missing_ok=True)
        return cached

    def presigned_url(self, key: str, expires_in: int = 3600) -> str:
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        if self.public_endpoint_url and self.endpoint_url:
            url = url.replace(self.endpoint_url, self.public_endpoint_url, 1)
        return url

    def serve(self, key: str, **send_file_kwargs):
        if self.public_endpoint_url:
            return redirect(self.presigned_url(key))
        return send_file(self.local_copy(key), **send_file_kwargs)


def _build_storage(config) -> Storage:
    backend = (config.get("STORAGE_BACKEND") or "local").lower()
    if backend == "local":
        return LocalStorage(config["UPLOAD_FOLDER"])
    if backend == "s3":
        return S3Storage(
            bucket=config["S3_BUCKET"],
            endpoint_url=config.get("S3_ENDPOINT_URL"),
            access_key_id=config.get("S3_ACCESS_KEY_ID"),
            secret_access_key=config.get("S3_SECRET_ACCESS_KEY"),
            region=config.get("S3_REGION"),
            public_endpoint_url=config.get("S3_PUBLIC_ENDPOINT_URL"),
        )
    raise ValueError(f"Unknown STORAGE_BACKEND: {backend!r}")


def get_storage() -> Storage:
    """Return the storage backend for the current app, creating it once."""
    extensions = current_app.extensions
    if "kalanjiyam_storage" not in extensions:
        extensions["kalanjiyam_storage"] = _build_storage(current_app.config)
    return extensions["kalanjiyam_storage"]
