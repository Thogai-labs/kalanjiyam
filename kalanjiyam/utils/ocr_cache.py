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
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, TypeVar

LOG = logging.getLogger(__name__)

T = TypeVar("T")

# Default Redis cache TTL: 7 days (604800 seconds)
DEFAULT_CACHE_TTL = 7 * 86400

# Default lock TTL and wait parameters for cache stampede coalescing
DEFAULT_STAMPEDE_LOCK_TTL = 10  # seconds
DEFAULT_STAMPEDE_WAIT_TIMEOUT = 3.0  # seconds
DEFAULT_STAMPEDE_POLL_INTERVAL = 0.05  # 50ms

_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

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


def _stampede_lock_key(resource_key: str) -> str:
    """Build Redis distributed lock key for request coalescing."""
    return f"lock:stampede:{resource_key}"


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
# Cache Stampede Prevention (Request Coalescing & Distributed Locking)
# ---------------------------------------------------------------------------


def acquire_stampede_lock(
    resource_key: str,
    lock_ttl: int = DEFAULT_STAMPEDE_LOCK_TTL,
) -> tuple[bool, str | None]:
    """Acquire a distributed lock for request coalescing on cache miss.

    Returns:
        (True, token) if the lock was acquired (Leader).
        (False, None) if another request already holds the lock (Follower).
    """
    client = get_redis_client()
    if client is None:
        return False, None

    token = uuid.uuid4().hex
    key = _stampede_lock_key(resource_key)
    try:
        acquired = bool(client.set(key, token, nx=True, ex=lock_ttl))
        return (acquired, token if acquired else None)
    except Exception as err:
        LOG.warning("Error acquiring stampede lock for %s: %s", resource_key, err)
        return False, None


def release_stampede_lock(resource_key: str, token: str) -> bool:
    """Safely release a distributed lock using token match via Lua script."""
    client = get_redis_client()
    if client is None or not token:
        return False

    key = _stampede_lock_key(resource_key)
    try:
        res = client.eval(_RELEASE_LOCK_LUA, 1, key, token)
        return bool(res)
    except Exception as err:
        LOG.warning("Error releasing stampede lock for %s: %s", resource_key, err)
        return False


def coalesce_cache_fetch(
    resource_key: str,
    fetch_fn: Callable[[], T],
    get_cached_fn: Callable[[], T | None],
    *,
    lock_ttl: int = DEFAULT_STAMPEDE_LOCK_TTL,
    wait_timeout: float = DEFAULT_STAMPEDE_WAIT_TIMEOUT,
    poll_interval: float = DEFAULT_STAMPEDE_POLL_INTERVAL,
) -> T:
    """Prevent cache stampedes / thundering herd by coalescing concurrent cache misses.

    Flow:
      1. Check if cache already contains the valid value (`get_cached_fn()`). If hit, returns immediately.
      2. Attempt to acquire distributed lock for `resource_key`.
      3. Leader (Lock acquired):
         - Double-checks cache.
         - Calls `fetch_fn()` (which fetches from S3/DB and populates the cache).
         - Releases lock in `finally` block and returns the result.
      4. Follower (Lock busy):
         - Waits and polls `get_cached_fn()` every `poll_interval` up to `wait_timeout`.
         - As soon as Leader writes to cache, Follower returns the cached result without hitting S3/DB.
         - If timeout expires, safely falls back to calling `fetch_fn()` directly.
    """
    # 1. Fast path: check cache first
    cached = get_cached_fn()
    if cached is not None:
        return cached

    # 2. Try acquiring distributed lock
    acquired, token = acquire_stampede_lock(resource_key, lock_ttl=lock_ttl)

    if acquired and token:
        # Leader: fetch from storage, write to cache, and release lock
        try:
            # Double check in case another worker just populated it
            cached_again = get_cached_fn()
            if cached_again is not None:
                return cached_again

            return fetch_fn()
        finally:
            release_stampede_lock(resource_key, token)

    # 3. Follower: another request is currently fetching/populating the cache
    LOG.debug("Cache stampede lock active for %s; coalescing request...", resource_key)
    start_time = time.time()
    while (time.time() - start_time) < wait_timeout:
        time.sleep(poll_interval)
        cached = get_cached_fn()
        if cached is not None:
            LOG.debug("Coalesced request satisfied from cache for %s after waiting.", resource_key)
            return cached

    # Fallback if leader took longer than wait_timeout or crashed
    LOG.warning(
        "Wait timeout (%ss) expired for %s stampede lock; falling back to direct fetch.",
        wait_timeout,
        resource_key,
    )
    return fetch_fn()


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
