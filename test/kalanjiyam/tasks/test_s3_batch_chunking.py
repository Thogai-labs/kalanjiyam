import pytest
from celery.exceptions import Retry
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from kalanjiyam import database as db
from kalanjiyam.queries import get_session
from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrChunk, BatchOcrPage
from kalanjiyam.tasks.s3_batch import (
    create_chunk_ranges,
    _finalize_batch_item_status,
    _finalize_batch_job_status,
    process_s3_batch_chunk,
    process_s3_batch_item,
    BATCH_OCR_CHUNK_SIZE,
)


def test_chunk_ranges_creation():
    assert create_chunk_ranges(1, 10) == [(1, 1)]
    assert create_chunk_ranges(10, 10) == [(1, 10)]
    assert create_chunk_ranges(11, 10) == [(1, 10), (11, 11)]
    
    ranges_400 = create_chunk_ranges(400, 10)
    assert len(ranges_400) == 40
    assert ranges_400[0] == (1, 10)
    assert ranges_400[-1] == (391, 400)


def test_chunk_idempotency(flask_app):
    with flask_app.app_context():
        session = get_session()
        job = BatchJob(target_uri="s3://test/bucket/test.pdf", status="IN_PROGRESS")
        session.add(job)
        session.flush()

        item = BatchItem(job_id=job.id, file_path="s3://test/bucket/test.pdf", status="IN_PROGRESS")
        session.add(item)
        session.flush()

        chunk = BatchOcrChunk(
            batch_item_id=item.id,
            start_page=1,
            end_page=10,
            status="COMPLETED",
            completed_at=datetime.utcnow()
        )
        session.add(chunk)
        session.commit()

        # Executing task for an already COMPLETED chunk must exit immediately
        with patch("kalanjiyam.tasks.s3_batch.httpx.Client") as mock_http:
            process_s3_batch_chunk(chunk.id)
            mock_http.assert_not_called()

        reloaded = session.query(BatchOcrChunk).get(chunk.id)
        assert reloaded.status == "COMPLETED"


def test_worker_crash_stale_lease_recovery(flask_app):
    with flask_app.app_context():
        session = get_session()
        job = BatchJob(target_uri="s3://test/bucket/stale.pdf", status="IN_PROGRESS")
        session.add(job)
        session.flush()

        item = BatchItem(job_id=job.id, file_path="s3://test/bucket/stale.pdf", status="IN_PROGRESS")
        session.add(item)
        session.flush()

        # Active lease (<15 min ago)
        active_chunk = BatchOcrChunk(
            batch_item_id=item.id,
            start_page=1,
            end_page=10,
            status="IN_PROGRESS",
            heartbeat_at=datetime.utcnow() - timedelta(minutes=2)
        )
        # Stale lease (>15 min ago)
        stale_chunk = BatchOcrChunk(
            batch_item_id=item.id,
            start_page=11,
            end_page=20,
            status="IN_PROGRESS",
            heartbeat_at=datetime.utcnow() - timedelta(minutes=30)
        )
        session.add_all([active_chunk, stale_chunk])
        session.commit()

        # Active lease chunk should be skipped
        with patch("kalanjiyam.tasks.s3_batch.httpx.Client") as mock_http:
            with pytest.raises(Retry):
                process_s3_batch_chunk(active_chunk.id)
            mock_http.assert_not_called()

        # Stale lease chunk should be reclaimed (attempt count incremented)
        with patch("kalanjiyam.tasks.s3_batch.httpx.Client") as mock_http:
            mock_client_instance = MagicMock()
            mock_http.return_value.__enter__.return_value = mock_client_instance
            process_s3_batch_chunk(stale_chunk.id)

        reloaded_stale = session.query(BatchOcrChunk).get(stale_chunk.id)
        assert reloaded_stale.attempt_count == 1


def test_item_and_job_finalization(flask_app):
    with flask_app.app_context():
        session = get_session()
        job = BatchJob(target_uri="s3://test/bucket/doc.pdf", status="IN_PROGRESS")
        session.add(job)
        session.flush()

        item = BatchItem(job_id=job.id, file_path="s3://test/bucket/doc.pdf", status="IN_PROGRESS")
        session.add(item)
        session.flush()

        chunk1 = BatchOcrChunk(batch_item_id=item.id, start_page=1, end_page=10, status="COMPLETED", total_ocr_latency_ms=1200)
        chunk2 = BatchOcrChunk(batch_item_id=item.id, start_page=11, end_page=20, status="COMPLETED", total_ocr_latency_ms=800)
        session.add_all([chunk1, chunk2])
        session.commit()

        _finalize_batch_item_status(session, item.id)

        reloaded_item = session.query(BatchItem).get(item.id)
        reloaded_job = session.query(BatchJob).get(job.id)

        assert reloaded_item.status == "COMPLETED"
        assert reloaded_item.total_ocr_latency_ms == 2000.0
        assert reloaded_job.status == "COMPLETED"


def test_preparation_redelivery_safety(flask_app):
    with flask_app.app_context():
        session = get_session()

        board = db.Board(title="test board")
        session.add(board)
        session.flush()

        proj = db.Project(slug="test-prep-proj", display_title="test prep", board_id=board.id)
        session.add(proj)
        session.flush()

        unreviewed = session.query(db.PageStatus).filter_by(name="reviewed-0").first()
        for p_i in range(1, 11):
            session.add(db.Page(project_id=proj.id, slug=str(p_i), order=p_i, status_id=unreviewed.id if unreviewed else 1))
        session.flush()

        job = BatchJob(target_uri="s3://test/bucket/prep.pdf", status="IN_PROGRESS")
        session.add(job)
        session.flush()

        item = BatchItem(job_id=job.id, file_path="s3://test/bucket/prep.pdf", status="IN_PROGRESS", project_id=proj.id)
        session.add(item)
        session.flush()

        chunk = BatchOcrChunk(batch_item_id=item.id, start_page=1, end_page=10, status="PENDING")
        session.add(chunk)
        session.commit()

        with patch("kalanjiyam.tasks.s3_batch._download_from_s3") as mock_dl, \
             patch("kalanjiyam.tasks.s3_batch.process_s3_batch_chunk.apply_async") as mock_dispatch:
            process_s3_batch_item(item.id)
            
            mock_dl.assert_not_called()
            assert mock_dispatch.called
