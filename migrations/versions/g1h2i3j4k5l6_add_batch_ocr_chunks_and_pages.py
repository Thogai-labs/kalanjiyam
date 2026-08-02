"""add batch_ocr_chunks and batch_ocr_pages tables

Revision ID: g1h2i3j4k5l6
Revises: f7a8b9c0d4e2
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "g1h2i3j4k5l6"
down_revision = "f7a8b9c0d4e2"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "batch_jobs" not in existing_tables:
        op.create_table(
            "batch_jobs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("target_uri", sa.String(length=1024), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    if "batch_items" not in existing_tables:
        op.create_table(
            "batch_items",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("job_id", sa.Integer(), nullable=False),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("mime_type", sa.String(length=128), nullable=True),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="PENDING"),
            sa.Column("source_size_bytes", sa.Integer(), nullable=True),
            sa.Column("extraction_latency_ms", sa.Float(), nullable=True),
            sa.Column("total_ocr_latency_ms", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["job_id"], ["batch_jobs.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["proof_projects.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "batch_ocr_chunks" not in existing_tables:
        op.create_table(
            "batch_ocr_chunks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("batch_item_id", sa.Integer(), nullable=False),
            sa.Column("start_page", sa.Integer(), nullable=False),
            sa.Column("end_page", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="PENDING"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("total_ocr_latency_ms", sa.Float(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["batch_item_id"], ["batch_items.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_item_id", "start_page", "end_page", name="uq_batch_ocr_chunk_range"),
        )
        op.create_index(op.f("ix_batch_ocr_chunks_batch_item_id"), "batch_ocr_chunks", ["batch_item_id"], unique=False)

    if "batch_ocr_pages" not in existing_tables:
        op.create_table(
            "batch_ocr_pages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("chunk_id", sa.Integer(), nullable=False),
            sa.Column("batch_item_id", sa.Integer(), nullable=False),
            sa.Column("page_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="PENDING"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["batch_item_id"], ["batch_items.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["chunk_id"], ["batch_ocr_chunks.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("chunk_id", "page_number", name="uq_batch_ocr_page_number"),
        )
        op.create_index(op.f("ix_batch_ocr_pages_batch_item_id"), "batch_ocr_pages", ["batch_item_id"], unique=False)
        op.create_index(op.f("ix_batch_ocr_pages_chunk_id"), "batch_ocr_pages", ["chunk_id"], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    if "batch_ocr_pages" in existing_tables:
        op.drop_index(op.f("ix_batch_ocr_pages_chunk_id"), table_name="batch_ocr_pages")
        op.drop_index(op.f("ix_batch_ocr_pages_batch_item_id"), table_name="batch_ocr_pages")
        op.drop_table("batch_ocr_pages")

    if "batch_ocr_chunks" in existing_tables:
        op.drop_index(op.f("ix_batch_ocr_chunks_batch_item_id"), table_name="batch_ocr_chunks")
        op.drop_table("batch_ocr_chunks")
