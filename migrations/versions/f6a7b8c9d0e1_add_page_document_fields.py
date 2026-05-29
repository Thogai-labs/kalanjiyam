"""Add page document fields for OCR replica editing.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-29
"""
from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    if not _has_column("proof_revisions", "document"):
        op.add_column("proof_revisions", sa.Column("document", sa.JSON(), nullable=True))
    if not _has_column("proof_revisions", "content_format"):
        op.add_column(
            "proof_revisions",
            sa.Column("content_format", sa.String(), nullable=False, server_default="plain"),
        )
    if not _has_column("proof_pages", "page_width"):
        op.add_column("proof_pages", sa.Column("page_width", sa.Integer(), nullable=True))
    if not _has_column("proof_pages", "page_height"):
        op.add_column("proof_pages", sa.Column("page_height", sa.Integer(), nullable=True))


def downgrade():
    if _has_column("proof_pages", "page_height"):
        op.drop_column("proof_pages", "page_height")
    if _has_column("proof_pages", "page_width"):
        op.drop_column("proof_pages", "page_width")
    if _has_column("proof_revisions", "content_format"):
        op.drop_column("proof_revisions", "content_format")
    if _has_column("proof_revisions", "document"):
        op.drop_column("proof_revisions", "document")
