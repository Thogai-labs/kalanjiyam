"""add proof_folders table

Revision ID: e7f8a9b0c1d2
Revises: 63e91d6cf80d
Create Date: 2026-09-21 18:25:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e7f8a9b0c1d2'
down_revision = '63e91d6cf80d'
branch_labels = None
depends_on = None


def _has_table(conn, table: str) -> bool:
    return table in sa.inspect(conn).get_table_names()


def upgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.create_table(
            "proof_folders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("parent_path", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("creator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fingerprint_id", sa.String(), nullable=True),
        )
        op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=True)
        op.create_index("ix_proof_folders_parent_path", "proof_folders", ["parent_path"], unique=False)
        op.create_index("ix_proof_folders_creator_id", "proof_folders", ["creator_id"], unique=False)
        op.create_index("ix_proof_folders_fingerprint_id", "proof_folders", ["fingerprint_id"], unique=False)
        return

    conn = op.get_bind()
    if not _has_table(conn, "proof_folders"):
        op.create_table(
            "proof_folders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("path", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("parent_path", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("creator_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("fingerprint_id", sa.String(), nullable=True),
        )
        op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=True)
        op.create_index("ix_proof_folders_parent_path", "proof_folders", ["parent_path"], unique=False)
        op.create_index("ix_proof_folders_creator_id", "proof_folders", ["creator_id"], unique=False)
        op.create_index("ix_proof_folders_fingerprint_id", "proof_folders", ["fingerprint_id"], unique=False)


def downgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.drop_table("proof_folders")
        return

    conn = op.get_bind()
    if _has_table(conn, "proof_folders"):
        op.drop_table("proof_folders")
