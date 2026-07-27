"""add default ocr engine to system settings

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d1"
down_revision = "d4e5f6a7b8c0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("system_settings", sa.Column("default_ocr_engine", sa.String(), nullable=False, server_default="tesseract"))


def downgrade():
    op.drop_column("system_settings", "default_ocr_engine")
