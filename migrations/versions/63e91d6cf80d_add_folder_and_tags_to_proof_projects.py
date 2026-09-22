"""add folder and tags to proof_projects

Revision ID: 63e91d6cf80d
Revises: w7x8y9z0a1b2
Create Date: 2026-09-21 16:36:23.191513

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '63e91d6cf80d'
down_revision = 'w7x8y9z0a1b2'
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _has_index(conn, table: str, index: str) -> bool:
    return index in {idx["name"] for idx in sa.inspect(conn).get_indexes(table)}


def upgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.add_column("proof_projects", sa.Column("folder", sa.String(), nullable=True))
        op.add_column("proof_projects", sa.Column("tags", sa.JSON(), nullable=True))
        op.create_index("ix_proof_projects_folder", "proof_projects", ["folder"], unique=False)
        return

    conn = op.get_bind()
    if not _has_column(conn, "proof_projects", "folder"):
        op.add_column(
            "proof_projects",
            sa.Column("folder", sa.String(), nullable=True),
        )
    if not _has_column(conn, "proof_projects", "tags"):
        op.add_column(
            "proof_projects",
            sa.Column("tags", sa.JSON(), nullable=True),
        )
    if not _has_index(conn, "proof_projects", "ix_proof_projects_folder"):
        op.create_index(
            "ix_proof_projects_folder",
            "proof_projects",
            ["folder"],
            unique=False,
        )


def downgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.drop_index("ix_proof_projects_folder", table_name="proof_projects")
        op.drop_column("proof_projects", "tags")
        op.drop_column("proof_projects", "folder")
        return

    conn = op.get_bind()
    if _has_index(conn, "proof_projects", "ix_proof_projects_folder"):
        op.drop_index("ix_proof_projects_folder", table_name="proof_projects")
    if _has_column(conn, "proof_projects", "tags"):
        op.drop_column("proof_projects", "tags")
    if _has_column(conn, "proof_projects", "folder"):
        op.drop_column("proof_projects", "folder")

