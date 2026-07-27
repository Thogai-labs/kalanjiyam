"""add guest upload limit to system settings

Revision ID: 9fd12345abcd
Revises: 8f1145005194
Create Date: 2026-06-30
"""

import sqlalchemy as sa
from alembic import op

revision = "9fd12345abcd"
down_revision = "8f1145005194"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "system_settings",
        sa.Column(
            "unregistered_user_upload_limit",
            sa.Integer(),
            nullable=False,
            server_default="10",
        ),
    )


def downgrade():
    op.drop_column("system_settings", "unregistered_user_upload_limit")
