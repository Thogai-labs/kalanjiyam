import concurrent.futures
import json
import time
from unittest.mock import MagicMock, patch
import pytest

from kalanjiyam.utils.ocr_cache import (
    acquire_stampede_lock,
    coalesce_cache_fetch,
    get_cached_ocr_document,
    get_cached_page_bboxes,
    get_cached_revision_document,
    invalidate_page_ocr_cache,
    invalidate_project_ocr_cache,
    release_stampede_lock,
    set_cached_ocr_document,
    set_cached_page_bboxes,
    set_cached_revision_document,
)


class MockRedis:
    """In-memory Redis mock for unit testing cache behavior and stampede locking."""
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def setex(self, key, ttl, value):
        self.store[key] = value

    def delete(self, *keys):
        count = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                count += 1
        return count

    def eval(self, script, numkeys, key, token):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def scan_iter(self, match="*", count=100):
        import fnmatch
        for k in list(self.store.keys()):
            if fnmatch.fnmatch(k, match):
                yield k


def test_cached_ocr_document_version_validation():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        doc_data = {"blocks": [{"content": "Page 1 OCR Text"}], "content_format": "blocks"}

        # 1. Store version 1 in Redis
        success = set_cached_ocr_document(
            project_slug="test-proj",
            page_slug="1",
            version_key="ocr:google",
            document=doc_data,
            version=1,
            revision_id=101,
        )
        assert success is True

        # 2. Get with matching expected version (v1) -> HIT
        cached = get_cached_ocr_document(
            project_slug="test-proj",
            page_slug="1",
            version_key="ocr:google",
            expected_version=1,
            expected_revision_id=101,
        )
        assert cached == doc_data

        # 3. Get with newer current version (v2 expected, but cached is v1) -> STALE -> INVALIDATED -> returns None
        cached_stale = get_cached_ocr_document(
            project_slug="test-proj",
            page_slug="1",
            version_key="ocr:google",
            expected_version=2,
            expected_revision_id=102,
        )
        assert cached_stale is None
        # Verify stale key was removed from Redis
        assert mock_redis.get("ocr:cache:page:test-proj:1:ocr:google") is None


def test_cached_page_bboxes_version_validation():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        bboxes_json = json.dumps([{"x1": 0, "y1": 0, "x2": 100, "y2": 100, "text": "Hello"}])

        # 1. Store bboxes in Redis
        set_cached_page_bboxes(
            project_slug="test-proj",
            page_slug="2",
            version_key="ocr:tesseract",
            bboxes_data=bboxes_json,
            version=1,
            revision_id=201,
        )

        # 2. Matching version -> HIT
        res = get_cached_page_bboxes(
            project_slug="test-proj",
            page_slug="2",
            version_key="ocr:tesseract",
            expected_version=1,
        )
        assert res == bboxes_json

        # 3. Revision mismatch -> STALE -> returns None & deletes key
        res_mismatch = get_cached_page_bboxes(
            project_slug="test-proj",
            page_slug="2",
            version_key="ocr:tesseract",
            expected_version=1,
            expected_revision_id=999,
        )
        assert res_mismatch is None
        assert mock_redis.get("ocr:cache:bboxes:test-proj:2:ocr:tesseract") is None


def test_revision_document_caching():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        rev_doc = {"blocks": [{"content": "Revision 500"}], "version": 5}

        set_cached_revision_document(500, rev_doc)
        cached = get_cached_revision_document(500)
        assert cached == rev_doc

        # Missing revision
        assert get_cached_revision_document(501) is None


def test_cache_invalidation_page_and_project():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        set_cached_ocr_document("book-a", "1", "ocr:google", {"data": 1})
        set_cached_page_bboxes("book-a", "1", "ocr:google", "[]")
        set_cached_ocr_document("book-a", "2", "ocr:google", {"data": 2})
        set_cached_ocr_document("book-b", "1", "ocr:google", {"data": 3})

        # Invalidate page 1 of book-a
        del_count = invalidate_page_ocr_cache("book-a", "1")
        assert del_count >= 2
        assert get_cached_ocr_document("book-a", "1", "ocr:google") is None
        # Page 2 of book-a and book-b should remain
        assert get_cached_ocr_document("book-a", "2", "ocr:google") == {"data": 2}
        assert get_cached_ocr_document("book-b", "1", "ocr:google") == {"data": 3}

        # Invalidate entire project book-a
        invalidate_project_ocr_cache("book-a")
        assert get_cached_ocr_document("book-a", "2", "ocr:google") is None
        assert get_cached_ocr_document("book-b", "1", "ocr:google") == {"data": 3}


def test_cache_handles_redis_exceptions_gracefully():
    failing_client = MagicMock()
    failing_client.get.side_effect = Exception("Redis connection refused")
    failing_client.set.side_effect = Exception("Redis connection refused")
    failing_client.setex.side_effect = Exception("Redis connection refused")
    failing_client.delete.side_effect = Exception("Redis connection refused")
    failing_client.eval.side_effect = Exception("Redis connection refused")

    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=failing_client):
        # Should not raise exception and return None / False
        assert get_cached_ocr_document("proj", "1", "ocr:google") is None
        assert set_cached_ocr_document("proj", "1", "ocr:google", {"test": 1}) is False
        assert get_cached_page_bboxes("proj", "1", "ocr:google") is None
        assert set_cached_page_bboxes("proj", "1", "ocr:google", "[]") is False
        assert invalidate_page_ocr_cache("proj", "1") == 0
        assert invalidate_project_ocr_cache("proj") == 0

        # Coalesce should fall back safely to fetch_fn
        called = False
        def _fetch():
            nonlocal called
            called = True
            return "direct_val"

        result = coalesce_cache_fetch("key", _fetch, lambda: None)
        assert result == "direct_val"
        assert called is True


def test_stampede_locking_acquire_and_release():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        # 1. First acquisition succeeds (Leader)
        acquired1, token1 = acquire_stampede_lock("resource-123")
        assert acquired1 is True
        assert token1 is not None

        # 2. Second acquisition for same resource fails (Follower)
        acquired2, token2 = acquire_stampede_lock("resource-123")
        assert acquired2 is False
        assert token2 is None

        # 3. Wrong token release fails
        released_wrong = release_stampede_lock("resource-123", "wrong-token")
        assert released_wrong is False

        # 4. Correct token release succeeds
        released_correct = release_stampede_lock("resource-123", token1)
        assert released_correct is True

        # 5. Lock can now be acquired again
        acquired3, token3 = acquire_stampede_lock("resource-123")
        assert acquired3 is True
        assert token3 is not None


def test_coalesce_cache_fetch_concurrent_stampede_prevention():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        fetch_call_count = 0
        cache_data = {}

        def _get_cached():
            return cache_data.get("heavy_page")

        def _fetch_from_s3():
            nonlocal fetch_call_count
            fetch_call_count += 1
            # Simulate S3 latency
            time.sleep(0.05)
            result = {"blocks": ["line 1", "line 2"]}
            cache_data["heavy_page"] = result
            return result

        # Simulate 10 simultaneous requests arriving at the same millisecond for a cold cache key
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(
                    coalesce_cache_fetch,
                    "heavy_page",
                    _fetch_from_s3,
                    _get_cached,
                    poll_interval=0.01,
                )
                for _ in range(10)
            ]
            results = [f.result() for f in futures]

        # ALL 10 requests must receive the correct data
        for res in results:
            assert res == {"blocks": ["line 1", "line 2"]}

        # CRITICAL: S3 fetch MUST be called ONLY ONCE, not 10 times!
        assert fetch_call_count == 1


def test_coalesce_cache_fetch_timeout_fallback():
    mock_redis = MockRedis()
    with patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis):
        # Simulate a dead leader that acquired the lock but crashed without populating cache
        acquire_stampede_lock("crashed_resource")

        called_fallback = False
        def _fetch_fallback():
            nonlocal called_fallback
            called_fallback = True
            return "recovered_data"

        # Wait timeout of 0.05s should expire and safely trigger fallback fetch
        res = coalesce_cache_fetch(
            "crashed_resource",
            _fetch_fallback,
            lambda: None,
            wait_timeout=0.05,
            poll_interval=0.01,
        )
        assert res == "recovered_data"
        assert called_fallback is True


def test_load_page_ocr_and_revision_invalidation_integration(flask_app):
    from kalanjiyam import database as db
    from kalanjiyam import queries as q
    from kalanjiyam.utils.document_storage import load_page_ocr
    from kalanjiyam.utils.revisions import add_revision

    mock_redis = MockRedis()
    with flask_app.app_context(), \
         patch("kalanjiyam.utils.ocr_cache.get_redis_client", return_value=mock_redis), \
         patch("kalanjiyam.utils.storage.get_storage") as mock_storage:

        mock_storage_instance = MagicMock()
        mock_storage_instance.load_json_gz.return_value = None
        mock_storage_instance.save_json_gz.return_value = True
        mock_storage.return_value = mock_storage_instance

        session = q.get_session()
        board = session.query(db.Board).first()
        status = session.query(db.PageStatus).first()
        user = session.query(db.User).first()

        project = db.Project(
            slug="test-ocr-cache-integration-proj",
            display_title="Test OCR Cache Integration Proj",
            board_id=board.id,
        )
        session.add(project)
        session.flush()

        page = db.Page(
            project_id=project.id,
            slug="1",
            order=1,
            status_id=status.id,
        )
        session.add(page)
        session.flush()

        doc_v1 = {
            "blocks": [
                {"bbox": [0, 0, 50, 50], "content": "Version 1 text", "reading_order": 1}
            ],
            "content_format": "blocks",
        }

        # Add initial revision (v1) on ocr:google track
        v1 = add_revision(
            page=page,
            summary="OCR run v1",
            content="Version 1 text",
            status="reviewed-0",
            version=0,
            author_id=user.id if user else None,
            document=doc_v1,
            content_format="blocks",
            version_key="ocr:google",
        )
        assert v1 == 1

        # 1. Call load_page_ocr -> Derives bboxes and caches into Redis
        bboxes_v1 = load_page_ocr(page)
        assert bboxes_v1 is not None
        assert "Version 1 text" in bboxes_v1
        assert "ocr:cache:bboxes:test-ocr-cache-integration-proj:1:ocr:google" in mock_redis.store

        # 2. Add revision v2 on ocr:google track
        doc_v2 = {
            "blocks": [
                {"bbox": [0, 0, 100, 100], "content": "Version 2 updated text", "reading_order": 1}
            ],
            "content_format": "blocks",
        }
        v2 = add_revision(
            page=page,
            summary="OCR run v2",
            content="Version 2 updated text",
            status="reviewed-0",
            version=1,
            author_id=user.id if user else None,
            document=doc_v2,
            content_format="blocks",
            version_key="ocr:google",
        )
        assert v2 == 2

        # 3. Call load_page_ocr -> Should detect version 2, re-derive, and serve latest Version 2 text
        session.refresh(page)
        bboxes_v2 = load_page_ocr(page)
        assert bboxes_v2 is not None
        assert "Version 2 updated text" in bboxes_v2
