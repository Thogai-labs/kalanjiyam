"""add system settings and user ocr credits

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    # Create system_settings table
    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("org_user_ocr_limit", sa.Integer(), nullable=True),
        sa.Column("org_user_storage_limit", sa.BigInteger(), nullable=True),
        sa.Column("registered_user_ocr_limit", sa.Integer(), nullable=True),
        sa.Column("registered_user_storage_limit", sa.BigInteger(), nullable=True),
        sa.Column("unregistered_user_ocr_limit", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("unregistered_user_project_limit", sa.Integer(), nullable=False, server_default="5"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add ocr_credits_used to users table
    op.add_column(
        "users",
        sa.Column("ocr_credits_used", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("users", "ocr_credits_used")
    op.drop_table("system_settings")
