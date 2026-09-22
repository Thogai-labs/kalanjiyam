"""add custom storage fields to groups

Revision ID: x1y2z3a4b5c6
Revises: w7x8y9z0a1b2
Create Date: 2026-09-22
"""

import sqlalchemy as sa
from alembic import op

revision = "x1y2z3a4b5c6"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "groups",
        sa.Column(
            "has_custom_storage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("groups", sa.Column("s3_bucket", sa.String(63), nullable=True))
    op.add_column("groups", sa.Column("s3_endpoint_url", sa.String(), nullable=True))
    op.add_column("groups", sa.Column("s3_region", sa.String(32), nullable=True))
    op.add_column("groups", sa.Column("s3_access_key_id", sa.String(), nullable=True))
    op.add_column(
        "groups", sa.Column("s3_secret_access_key", sa.String(), nullable=True)
    )


def downgrade():
    op.drop_column("groups", "s3_secret_access_key")
    op.drop_column("groups", "s3_access_key_id")
    op.drop_column("groups", "s3_region")
    op.drop_column("groups", "s3_endpoint_url")
    op.drop_column("groups", "s3_bucket")
    op.drop_column("groups", "has_custom_storage")
