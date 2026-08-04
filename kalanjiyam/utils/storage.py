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
import time
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


def project_docx_key(project_slug: str) -> str:
    """Key of a project's source DOCX."""
    return f"projects/{project_slug}/docx/source.docx"


def docx_upload_key(docx_id: str) -> str:
    """Key of an uploaded DOCX file for standalone translation."""
    return f"docx/uploads/{docx_id}.docx"


def docx_translation_key(docx_id: str) -> str:
    """Key of a translated DOCX file for standalone translation."""
    return f"docx/translations/{docx_id}.docx"


def page_ocr_key(project_slug: str, page_slug: str) -> str:
    """Key for a page's raw OCR bounding-box payload (gzipped JSON)."""
    return f"projects/{project_slug}/ocr/{page_slug}.json.gz"


def revision_document_key(
    project_slug: str, page_slug: str, version_num: int, tag: str = ""
) -> str:
    """Key for a revision's structured block document snapshot (gzipped JSON)."""
    prefix = f"{tag}_" if tag else ""
    return f"projects/{project_slug}/revisions/{page_slug}/{prefix}v{version_num}.json.gz"


def comparison_result_key(project_slug: str, comparison_id: int) -> str:
    """Key for detailed per-page OCR comparison results (gzipped JSON)."""
    return f"projects/{project_slug}/comparisons/{comparison_id}.json.gz"


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
    def delete(self, key: str) -> bool:
        """Delete a single object by key. Returns True if deleted."""

    @abstractmethod
    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        """Yield ``(key, size_in_bytes)`` for every object under `prefix`."""

    @abstractmethod
    def list_keys_with_mtime(self, prefix: str = "") -> Iterator[tuple[str, int, float]]:
        """Yield ``(key, size_in_bytes, mtime_timestamp)`` for objects under `prefix`."""

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

    # Convenience methods for gzipped JSON payloads
    # ----------------------------------------------

    def save_json_gz(self, key: str, data: dict | list | str) -> None:
        """Serialize *data* as gzipped JSON and store under *key*.

        Works across all backends (local, S3/VersityGW, memory).  For the
        S3 backend the object is stored with ``Content-Encoding: gzip`` so
        that browsers decompress it transparently.

        *data* may be a dict/list (serialized via ``json.dumps``) or a
        pre-serialized string.
        """
        import gzip as _gzip
        import json as _json

        raw = (
            data.encode("utf-8")
            if isinstance(data, str)
            else _json.dumps(data, ensure_ascii=False).encode("utf-8")
        )
        self.save(key, _gzip.compress(raw))

    def load_json_gz(self, key: str) -> dict | list | str | None:
        """Fetch a gzipped JSON object and return the deserialized value.

        Returns ``None`` if the key does not exist.
        """
        import gzip as _gzip
        import json as _json

        try:
            compressed = self.read_bytes(key)
        except FileNotFoundError:
            return None
        decompressed = _gzip.decompress(compressed).decode("utf-8")
        try:
            return _json.loads(decompressed)
        except _json.JSONDecodeError:
            # The payload was plain text (e.g. bounding-box format), not JSON.
            return decompressed


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

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.is_file():
            path.unlink()
            return True
        return False

    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        base = self._path(prefix)
        if not base.is_dir():
            return
        for path in base.rglob("*"):
            if path.is_file():
                yield path.relative_to(self.root).as_posix(), path.stat().st_size

    def list_keys_with_mtime(self, prefix: str = "") -> Iterator[tuple[str, int, float]]:
        base = self._path(prefix) if prefix else self.root
        if not base.is_dir():
            return
        for path in base.rglob("*"):
            if path.is_file():
                st = path.stat()
                yield path.relative_to(self.root).as_posix(), st.st_size, st.st_mtime

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
            if hasattr(source, "seek"):
                try:
                    source.seek(0)
                except (AttributeError, OSError, TypeError):
                    pass
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

    def delete(self, key: str) -> bool:
        if self.exists(key):
            self.client.delete_object(Bucket=self.bucket, Key=key)
            return True
        return False

    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["Size"]

    def list_keys_with_mtime(self, prefix: str = "") -> Iterator[tuple[str, int, float]]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                lm = obj.get("LastModified")
                mtime = lm.timestamp() if lm else time.time()
                yield obj["Key"], obj["Size"], mtime

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
class MemoryStorage(Storage):
    """In-memory storage backend for testing."""

    def __init__(self):
        self.files = {}
        self.mtimes = {}

    def save(self, key: str, source: Path | str | bytes | BinaryIO, mtime: float | None = None) -> None:
        if isinstance(source, (Path, str)):
            self.files[key] = Path(source).read_bytes()
        elif isinstance(source, bytes):
            self.files[key] = source
        else:
            self.files[key] = source.read()
        self.mtimes[key] = mtime or time.time()

    def read_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(f"memory:///{key}")
        return self.files[key]

    def exists(self, key: str) -> bool:
        return key in self.files

    def delete(self, key: str) -> bool:
        if key in self.files:
            del self.files[key]
            self.mtimes.pop(key, None)
            return True
        return False

    def list_keys(self, prefix: str) -> Iterator[tuple[str, int]]:
        for key, data in list(self.files.items()):
            if key.startswith(prefix):
                yield key, len(data)

    def list_keys_with_mtime(self, prefix: str = "") -> Iterator[tuple[str, int, float]]:
        for key, data in list(self.files.items()):
            if key.startswith(prefix):
                yield key, len(data), self.mtimes.get(key, time.time())

    def delete_prefix(self, prefix: str) -> int:
        to_delete = [k for k in self.files if k.startswith(prefix)]
        for k in to_delete:
            del self.files[k]
            self.mtimes.pop(k, None)
        return len(to_delete)

    def local_copy(self, key: str) -> Path:
        if key not in self.files:
            return Path(tempfile.gettempdir()) / f"missing-{uuid.uuid4().hex}"
        temp_dir = Path(tempfile.gettempdir()) / "kalanjiyam-memory-storage"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_file = temp_dir / Path(key).name
        temp_file.write_bytes(self.files[key])
        return temp_file

    def serve(self, key: str, **send_file_kwargs):
        import io
        return send_file(io.BytesIO(self.read_bytes(key)), **send_file_kwargs)


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


def cleanup_old_uploaded_files(
    storage: Storage,
    days: int = 7,
    extensions: tuple[str, ...] = (".pdf", ".docx", ".doc"),
) -> int:
    """Delete uploaded files matching `extensions` older than `days` days.

    :param storage: Storage backend instance.
    :param days: Age threshold in days (default: 7).
    :param extensions: Target file extensions to clean up.
    :return: Number of files deleted.
    """
    cutoff = time.time() - (days * 86400)
    deleted_count = 0
    for key, _size, mtime in storage.list_keys_with_mtime(""):
        lower_key = key.lower()
        if any(lower_key.endswith(ext) for ext in extensions):
            if mtime <= cutoff:
                if storage.delete(key):
                    deleted_count += 1
    return deleted_count
