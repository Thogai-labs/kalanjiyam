"""add min_confidence and low_conf_page_count to batch_items

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
"""

import sqlalchemy as sa
from alembic import op

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_items")
    if "min_confidence" not in columns:
        op.add_column("batch_items", sa.Column("min_confidence", sa.Float(), nullable=True))
    if "low_conf_page_count" not in columns:
        op.add_column("batch_items", sa.Column("low_conf_page_count", sa.Integer(), nullable=True))


def downgrade():
    pass
