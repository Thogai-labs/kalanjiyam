"""add indexes for batch jobs, batch items and metrics

Revision ID: u5v6w7x8y9z0
Revises: t4u5v6w7x8y9
"""

import sqlalchemy as sa
from alembic import op

revision = "u5v6w7x8y9z0"
down_revision = "t4u5v6w7x8y9"
branch_labels = None
depends_on = None


def _indexes(table):
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    job_indexes = _indexes("batch_jobs")
    if "ix_batch_jobs_job_type" not in job_indexes:
        op.create_index("ix_batch_jobs_job_type", "batch_jobs", ["job_type"])
    if "ix_batch_jobs_status" not in job_indexes:
        op.create_index("ix_batch_jobs_status", "batch_jobs", ["status"])
    if "ix_batch_jobs_created_at" not in job_indexes:
        op.create_index("ix_batch_jobs_created_at", "batch_jobs", ["created_at"])

    item_indexes = _indexes("batch_items")
    if "ix_batch_items_job_id" not in item_indexes:
        op.create_index("ix_batch_items_job_id", "batch_items", ["job_id"])
    if "ix_batch_items_project_id" not in item_indexes:
        op.create_index("ix_batch_items_project_id", "batch_items", ["project_id"])
    if "ix_batch_items_status" not in item_indexes:
        op.create_index("ix_batch_items_status", "batch_items", ["status"])
    if "ix_batch_items_created_at" not in item_indexes:
        op.create_index("ix_batch_items_created_at", "batch_items", ["created_at"])

    page_indexes = _indexes("batch_ocr_pages")
    if "ix_batch_ocr_pages_status" not in page_indexes:
        op.create_index("ix_batch_ocr_pages_status", "batch_ocr_pages", ["status"])


def downgrade():
    page_indexes = _indexes("batch_ocr_pages")
    if "ix_batch_ocr_pages_status" in page_indexes:
        op.drop_index("ix_batch_ocr_pages_status", table_name="batch_ocr_pages")

    item_indexes = _indexes("batch_items")
    if "ix_batch_items_created_at" in item_indexes:
        op.drop_index("ix_batch_items_created_at", table_name="batch_items")
    if "ix_batch_items_status" in item_indexes:
        op.drop_index("ix_batch_items_status", table_name="batch_items")
    if "ix_batch_items_project_id" in item_indexes:
        op.drop_index("ix_batch_items_project_id", table_name="batch_items")
    if "ix_batch_items_job_id" in item_indexes:
        op.drop_index("ix_batch_items_job_id", table_name="batch_items")

    job_indexes = _indexes("batch_jobs")
    if "ix_batch_jobs_created_at" in job_indexes:
        op.drop_index("ix_batch_jobs_created_at", table_name="batch_jobs")
    if "ix_batch_jobs_status" in job_indexes:
        op.drop_index("ix_batch_jobs_status", table_name="batch_jobs")
    if "ix_batch_jobs_job_type" in job_indexes:
        op.drop_index("ix_batch_jobs_job_type", table_name="batch_jobs")
