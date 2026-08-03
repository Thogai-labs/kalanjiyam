"""add recommended ocr engine to system settings

Revision ID: f6a7b8c9d3e1
Revises: e5f6a7b8c9d2
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d3e1"
down_revision = "e5f6a7b8c9d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("system_settings", sa.Column("recommended_ocr_engine", sa.String(), nullable=True))


def downgrade():
    op.drop_column("system_settings", "recommended_ocr_engine")
