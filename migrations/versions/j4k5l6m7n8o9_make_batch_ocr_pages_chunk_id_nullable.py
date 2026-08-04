"""make batch_ocr_pages chunk_id nullable and update unique constraint

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
"""

import sqlalchemy as sa
from alembic import op

revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("batch_ocr_pages") as batch_op:
        batch_op.alter_column("chunk_id", existing_type=sa.Integer(), nullable=True)
        try:
            batch_op.drop_constraint("uq_batch_ocr_page_number", type_="unique")
        except Exception:
            pass
        batch_op.create_unique_constraint("uq_batch_ocr_page_number", ["batch_item_id", "page_number"])


def downgrade():
    with op.batch_alter_table("batch_ocr_pages") as batch_op:
        try:
            batch_op.drop_constraint("uq_batch_ocr_page_number", type_="unique")
        except Exception:
            pass
        batch_op.create_unique_constraint("uq_batch_ocr_page_number", ["chunk_id", "page_number"])
        batch_op.alter_column("chunk_id", existing_type=sa.Integer(), nullable=False)
