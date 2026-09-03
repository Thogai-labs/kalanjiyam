"""add default and recommended translation engines to system settings

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-08-27
"""

import sqlalchemy as sa
from alembic import op

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "system_settings",
        sa.Column(
            "default_translation_engine",
            sa.String(),
            nullable=False,
            server_default="indictrans2",
        ),
    )
    op.add_column(
        "system_settings",
        sa.Column("recommended_translation_engine", sa.String(), nullable=True),
    )


def downgrade():
    op.drop_column("system_settings", "recommended_translation_engine")
    op.drop_column("system_settings", "default_translation_engine")
