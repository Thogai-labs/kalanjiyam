"""Redis caching, version validation, and cache invalidation for OCR data.

Provides a version-aware caching layer for OCR documents, bounding box payloads,
and page revision documents in Redis.

Cached payloads are wrapped in a version envelope:
    {
        "version": <int: PageVersion.version>,
        "revision_id": <int: Revision.id>,
        "version_key": <str: e.g. "ocr:google">,
        "updated_at": <str: ISO timestamp>,
        "cached_at": <str: ISO timestamp>,
        "data": <Any: document dict, bboxes JSON, etc.>
    }

When reading from cache, the envelope is validated against the current version
and/or revision ID. If stale, the cached entry is automatically deleted and
re-fetched from source storage (S3 / DB).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from typing import Any

LOG = logging.getLogger(__name__)

# Default Redis cache TTL: 7 days (604800 seconds)
DEFAULT_CACHE_TTL = 7 * 86400

_redis_client = None


def get_redis_client():
    """Return a Redis client singleton instance."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis

            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            _redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        except Exception as e:
            LOG.warning("Failed to initialize Redis client for OCR cache: %s", e)
            return None
    return _redis_client


def _ocr_page_cache_key(project_slug: str, page_slug: str, version_key: str) -> str:
    """Build Redis key for a page version's structured OCR document."""
    return f"ocr:cache:page:{project_slug}:{page_slug}:{version_key}"


def _ocr_bboxes_cache_key(project_slug: str, page_slug: str, version_key: str) -> str:
    """Build Redis key for page OCR bounding boxes."""
    return f"ocr:cache:bboxes:{project_slug}:{page_slug}:{version_key}"


def _revision_doc_cache_key(revision_id: int) -> str:
    """Build Redis key for a specific revision document."""
    return f"ocr:cache:rev:{revision_id}"


# ---------------------------------------------------------------------------
# Version Validation & Retrieval
# ---------------------------------------------------------------------------


def get_cached_ocr_document(
    project_slug: str,
    page_slug: str,
    version_key: str,
    *,
    expected_version: int | None = None,
    expected_revision_id: int | None = None,
) -> dict | None:
    """Fetch cached OCR document from Redis, validating version and revision integrity.

    Returns:
        The cached document dict if present and version matches, else ``None``.
        If the cached version is stale, the stale key is deleted from Redis.
    """
    client = get_redis_client()
    if client is None:
        return None

    key = _ocr_page_cache_key(project_slug, page_slug, version_key)
    try:
        raw = client.get(key)
        if not raw:
            return None

        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            client.delete(key)
            return None

        cached_version = envelope.get("version")
        cached_rev_id = envelope.get("revision_id")

        # Validate version if expected_version is provided
        if expected_version is not None and cached_version != expected_version:
            LOG.info(
                "Stale OCR document cache for %s/%s (%s): cached v%s != expected v%s. Invalidating.",
                project_slug,
                page_slug,
                version_key,
                cached_version,
                expected_version,
            )
            client.delete(key)
            return None

        # Validate revision_id if expected_revision_id is provided
        if expected_revision_id is not None and cached_rev_id != expected_revision_id:
            LOG.info(
                "Stale OCR document cache for %s/%s (%s): cached rev %s != expected rev %s. Invalidating.",
                project_slug,
                page_slug,
                version_key,
                cached_rev_id,
                expected_revision_id,
            )
            client.delete(key)
            return None

        return envelope.get("data")
    except Exception as err:
        LOG.warning("Error reading OCR document from Redis cache (%s): %s", key, err)
        return None


def set_cached_ocr_document(
    project_slug: str,
    page_slug: str,
    version_key: str,
    document: dict,
    *,
    version: int = 1,
    revision_id: int | None = None,
    ttl: int = DEFAULT_CACHE_TTL,
) -> bool:
    """Store OCR document in Redis inside a version envelope."""
    client = get_redis_client()
    if client is None or not isinstance(document, dict):
        return False

    key = _ocr_page_cache_key(project_slug, page_slug, version_key)
    envelope = {
        "version": version,
        "revision_id": revision_id,
        "version_key": version_key,
        "cached_at": datetime.now(UTC).isoformat(),
        "data": document,
    }
    try:
        client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False))
        return True
    except Exception as err:
        LOG.warning("Error caching OCR document in Redis (%s): %s", key, err)
        return False


def get_cached_page_bboxes(
    project_slug: str,
    page_slug: str,
    version_key: str,
    *,
    expected_version: int | None = None,
    expected_revision_id: int | None = None,
) -> str | None:
    """Fetch cached OCR bounding boxes JSON from Redis with version validation."""
    client = get_redis_client()
    if client is None:
        return None

    key = _ocr_bboxes_cache_key(project_slug, page_slug, version_key)
    try:
        raw = client.get(key)
        if not raw:
            return None

        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            client.delete(key)
            return None

        cached_version = envelope.get("version")
        cached_rev_id = envelope.get("revision_id")

        if expected_version is not None and cached_version != expected_version:
            LOG.info(
                "Stale OCR bounding boxes cache for %s/%s (%s): cached v%s != expected v%s. Invalidating.",
                project_slug,
                page_slug,
                version_key,
                cached_version,
                expected_version,
            )
            client.delete(key)
            return None

        if expected_revision_id is not None and cached_rev_id != expected_revision_id:
            LOG.info(
                "Stale OCR bounding boxes cache for %s/%s (%s): cached rev %s != expected rev %s. Invalidating.",
                project_slug,
                page_slug,
                version_key,
                cached_rev_id,
                expected_revision_id,
            )
            client.delete(key)
            return None

        data = envelope.get("data")
        return data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    except Exception as err:
        LOG.warning("Error reading OCR bounding boxes from Redis cache (%s): %s", key, err)
        return None


def set_cached_page_bboxes(
    project_slug: str,
    page_slug: str,
    version_key: str,
    bboxes_data: str | list | dict,
    *,
    version: int = 1,
    revision_id: int | None = None,
    ttl: int = DEFAULT_CACHE_TTL,
) -> bool:
    """Store OCR bounding boxes in Redis inside a version envelope."""
    client = get_redis_client()
    if client is None or bboxes_data is None:
        return False

    key = _ocr_bboxes_cache_key(project_slug, page_slug, version_key)
    envelope = {
        "version": version,
        "revision_id": revision_id,
        "version_key": version_key,
        "cached_at": datetime.now(UTC).isoformat(),
        "data": bboxes_data,
    }
    try:
        client.setex(key, ttl, json.dumps(envelope, ensure_ascii=False))
        return True
    except Exception as err:
        LOG.warning("Error caching OCR bounding boxes in Redis (%s): %s", key, err)
        return False


def get_cached_revision_document(revision_id: int) -> dict | None:
    """Load cached revision document by revision_id (immutable once created)."""
    client = get_redis_client()
    if client is None:
        return None

    key = _revision_doc_cache_key(revision_id)
    try:
        raw = client.get(key)
        if not raw:
            return None
        return json.loads(raw)
    except Exception as err:
        LOG.warning("Error reading revision document %s from Redis cache: %s", revision_id, err)
        return None


def set_cached_revision_document(
    revision_id: int,
    document: dict,
    ttl: int = DEFAULT_CACHE_TTL,
) -> bool:
    """Store revision document in Redis cache."""
    client = get_redis_client()
    if client is None or not isinstance(document, dict):
        return False

    key = _revision_doc_cache_key(revision_id)
    try:
        client.setex(key, ttl, json.dumps(document, ensure_ascii=False))
        return True
    except Exception as err:
        LOG.warning("Error caching revision document %s in Redis: %s", revision_id, err)
        return False


# ---------------------------------------------------------------------------
# Cache Invalidation Utilities
# ---------------------------------------------------------------------------


def invalidate_page_ocr_cache(
    project_slug: str,
    page_slug: str,
    version_key: str | None = None,
) -> int:
    """Invalidate cached OCR documents and bounding boxes for a specific page.

    If ``version_key`` is provided, invalidates entries for that specific track.
    If ``version_key`` is ``None``, invalidates all cached tracks for the page.
    """
    client = get_redis_client()
    if client is None:
        return 0

    deleted_count = 0
    try:
        if version_key:
            keys_to_delete = [
                _ocr_page_cache_key(project_slug, page_slug, version_key),
                _ocr_bboxes_cache_key(project_slug, page_slug, version_key),
            ]
            deleted_count = client.delete(*keys_to_delete)
        else:
            # Match all keys for this page
            patterns = [
                f"ocr:cache:page:{project_slug}:{page_slug}:*",
                f"ocr:cache:bboxes:{project_slug}:{page_slug}:*",
            ]
            for pattern in patterns:
                keys = list(client.scan_iter(match=pattern, count=100))
                if keys:
                    deleted_count += client.delete(*keys)
        LOG.debug(
            "Invalidated %d OCR cache keys for %s/%s (version_key=%s)",
            deleted_count,
            project_slug,
            page_slug,
            version_key,
        )
    except Exception as err:
        LOG.warning("Error invalidating page OCR cache in Redis: %s", err)

    return deleted_count


def invalidate_project_ocr_cache(project_slug: str) -> int:
    """Invalidate all OCR cache entries for an entire project."""
    client = get_redis_client()
    if client is None:
        return 0

    deleted_count = 0
    try:
        patterns = [
            f"ocr:cache:page:{project_slug}:*",
            f"ocr:cache:bboxes:{project_slug}:*",
        ]
        for pattern in patterns:
            keys = list(client.scan_iter(match=pattern, count=200))
            if keys:
                deleted_count += client.delete(*keys)
        LOG.info("Invalidated %d project OCR cache keys for %s", deleted_count, project_slug)
    except Exception as err:
        LOG.warning("Error invalidating project OCR cache in Redis for %s: %s", project_slug, err)

    return deleted_count
