"""add extract_metadata to batch_jobs

Revision ID: s3t4u5v6w7x8
Revises: r2s3t4u5v6w7
"""

import sqlalchemy as sa
from alembic import op

revision = "s3t4u5v6w7x8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("batch_jobs")
    if "extract_metadata" not in columns:
        op.add_column(
            "batch_jobs",
            sa.Column("extract_metadata", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade():
    columns = _columns("batch_jobs")
    if "extract_metadata" in columns:
        op.drop_column("batch_jobs", "extract_metadata")
