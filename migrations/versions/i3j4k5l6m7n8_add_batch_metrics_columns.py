"""add batch items and batch ocr pages metrics columns

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
"""

import sqlalchemy as sa
from alembic import op

revision = "i3j4k5l6m7n8"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_items")
    if "extracted_images_size_bytes" not in columns:
        op.add_column("batch_items", sa.Column("extracted_images_size_bytes", sa.Integer(), nullable=True))
    if "cropped_images_size_bytes" not in columns:
        op.add_column("batch_items", sa.Column("cropped_images_size_bytes", sa.Integer(), nullable=True))
    if "ocr_data_size_bytes" not in columns:
        op.add_column("batch_items", sa.Column("ocr_data_size_bytes", sa.Integer(), nullable=True))
    if "translation_data_size_bytes" not in columns:
        op.add_column("batch_items", sa.Column("translation_data_size_bytes", sa.Integer(), nullable=True))
    if "source_lang" not in columns:
        op.add_column("batch_items", sa.Column("source_lang", sa.String(32), nullable=True))
    if "target_lang" not in columns:
        op.add_column("batch_items", sa.Column("target_lang", sa.String(32), nullable=True))
    if "total_translation_latency_ms" not in columns:
        op.add_column("batch_items", sa.Column("total_translation_latency_ms", sa.Float(), nullable=True))

    columns = _columns("batch_ocr_pages")
    if "ocr_latency_ms" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("ocr_latency_ms", sa.Float(), nullable=True))
    if "translation_latency_ms" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("translation_latency_ms", sa.Float(), nullable=True))
    if "extracted_image_size_bytes" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("extracted_image_size_bytes", sa.Integer(), nullable=True))
    if "cropped_image_size_bytes" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("cropped_image_size_bytes", sa.Integer(), nullable=True))
    if "ocr_data_size_bytes" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("ocr_data_size_bytes", sa.Integer(), nullable=True))
    if "translation_data_size_bytes" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("translation_data_size_bytes", sa.Integer(), nullable=True))
    if "source_lang" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("source_lang", sa.String(32), nullable=True))
    if "target_lang" not in columns:
        op.add_column("batch_ocr_pages", sa.Column("target_lang", sa.String(32), nullable=True))


def downgrade():
    pass
