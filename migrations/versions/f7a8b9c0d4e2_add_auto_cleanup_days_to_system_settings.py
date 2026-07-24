"""add auto_cleanup_days to system settings

Revision ID: f7a8b9c0d4e2
Revises: f6a7b8c9d3e1
Create Date: 2026-07-24
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d4e2"
down_revision = ("f6a7b8c9d3e1", "a2b3c4d5e6f7")
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "system_settings",
        sa.Column("auto_cleanup_days", sa.Integer(), nullable=False, server_default="7"),
    )


def downgrade():
    op.drop_column("system_settings", "auto_cleanup_days")
