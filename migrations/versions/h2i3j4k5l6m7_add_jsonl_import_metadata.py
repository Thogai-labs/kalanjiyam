"""add JSONL import tracking metadata

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
"""

import sqlalchemy as sa
from alembic import op

revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_jobs")
    if "jsonl_uri" not in columns:
        op.add_column("batch_jobs", sa.Column("jsonl_uri", sa.String(1024), nullable=True))
    if "pdf_uri" not in columns:
        op.add_column("batch_jobs", sa.Column("pdf_uri", sa.String(1024), nullable=True))
    if "job_type" not in columns:
        op.add_column("batch_jobs", sa.Column("job_type", sa.String(64), nullable=False, server_default="BATCH_OCR"))
    columns = _columns("batch_items")
    if "source_book_id" not in columns:
        op.add_column("batch_items", sa.Column("source_book_id", sa.String(255), nullable=True))
        op.create_index("ix_batch_items_source_book_id", "batch_items", ["source_book_id"])
    if "source_jsonl_uri" not in columns:
        op.add_column("batch_items", sa.Column("source_jsonl_uri", sa.String(1024), nullable=True))
    if "total_pages" not in columns:
        op.add_column("batch_items", sa.Column("total_pages", sa.Integer(), nullable=True))
    columns = _columns("proof_projects")
    if "source_book_id" not in columns:
        op.add_column("proof_projects", sa.Column("source_book_id", sa.String(255), nullable=True))
        op.create_index("ix_proof_projects_source_book_id", "proof_projects", ["source_book_id"], unique=True)


def downgrade():
    pass
