"""add group per user limits

Revision ID: d4e5f6a7b8c0
Revises: c3d4e5f6a7b8
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    # Add columns to groups table
    op.add_column("groups", sa.Column("default_user_storage_limit", sa.BigInteger(), nullable=True))
    op.add_column("groups", sa.Column("default_user_ocr_limit", sa.Integer(), nullable=True))

    # Remove columns from system_settings table
    op.drop_column("system_settings", "org_user_ocr_limit")
    op.drop_column("system_settings", "org_user_storage_limit")
    op.drop_column("system_settings", "registered_user_ocr_limit")
    op.drop_column("system_settings", "registered_user_storage_limit")


def downgrade():
    op.add_column("system_settings", sa.Column("registered_user_storage_limit", sa.BigInteger(), nullable=True))
    op.add_column("system_settings", sa.Column("registered_user_ocr_limit", sa.Integer(), nullable=True))
    op.add_column("system_settings", sa.Column("org_user_storage_limit", sa.BigInteger(), nullable=True))
    op.add_column("system_settings", sa.Column("org_user_ocr_limit", sa.Integer(), nullable=True))

    op.drop_column("groups", "default_user_ocr_limit")
    op.drop_column("groups", "default_user_storage_limit")
