"""add ocr metrics columns (engine, confidence, p05, blocks, chars, engine_latency_ms)

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
"""

import sqlalchemy as sa
from alembic import op

revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_items")
    if "engine" not in columns:
        op.add_column("batch_items", sa.Column("engine", sa.String(64), nullable=True))
    if "avg_confidence" not in columns:
        op.add_column("batch_items", sa.Column("avg_confidence", sa.Float(), nullable=True))
    if "avg_p05" not in columns:
        op.add_column("batch_items", sa.Column("avg_p05", sa.Float(), nullable=True))
    if "total_blocks" not in columns:
        op.add_column("batch_items", sa.Column("total_blocks", sa.Integer(), nullable=True))
    if "total_chars" not in columns:
        op.add_column("batch_items", sa.Column("total_chars", sa.Integer(), nullable=True))
    if "total_engine_latency_ms" not in columns:
        op.add_column("batch_items", sa.Column("total_engine_latency_ms", sa.Float(), nullable=True))

    columns = _columns("batch_ocr_pages")
    if "engine" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("engine", sa.String(64), nullable=True))
    if "confidence" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("confidence", sa.Float(), nullable=True))
    if "p05" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("p05", sa.Float(), nullable=True))
    if "blocks" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("blocks", sa.Integer(), nullable=True))
    if "chars" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("chars", sa.Integer(), nullable=True))
    if "engine_latency_ms" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("engine_latency_ms", sa.Float(), nullable=True))


def downgrade():
    pass
