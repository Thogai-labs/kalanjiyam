"""add page_type column to batch_items and batch_ocr_pages

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
"""

import sqlalchemy as sa
from alembic import op

revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_items")
    if "page_type" not in columns:
        op.add_column("batch_items", sa.Column("page_type", sa.String(32), nullable=True, server_default="original"))

    columns = _columns("batch_ocr_pages")
    if "page_type" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("page_type", sa.String(32), nullable=True, server_default="original"))


def downgrade():
    pass
