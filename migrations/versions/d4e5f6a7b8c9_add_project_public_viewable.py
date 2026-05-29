"""Add proof_projects.is_publicly_viewable for org-public books.

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-05-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "d4e5f6a7b8c9"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "proof_projects", "is_publicly_viewable"):
        op.add_column(
            "proof_projects",
            sa.Column(
                "is_publicly_viewable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_column(conn, "proof_projects", "is_publicly_viewable"):
        op.drop_column("proof_projects", "is_publicly_viewable")
