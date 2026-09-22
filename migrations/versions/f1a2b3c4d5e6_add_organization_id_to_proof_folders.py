"""add organization_id to proof_folders

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-09-22 16:40:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e7f8a9b0c1d2'
branch_labels = None
depends_on = None


def _has_table(conn, table: str) -> bool:
    return table in sa.inspect(conn).get_table_names()


def _has_column(conn, table: str, column: str) -> bool:
    cols = [c["name"] for c in sa.inspect(conn).get_columns(table)]
    return column in cols


def upgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.add_column("proof_folders", sa.Column("organization_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True))
        op.create_index("ix_proof_folders_organization_id", "proof_folders", ["organization_id"], unique=False)
        op.drop_index("ix_proof_folders_path", table_name="proof_folders")
        op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=False)
        op.create_unique_constraint("uq_proof_folders_path_org", "proof_folders", ["path", "organization_id"])
        return

    conn = op.get_bind()
    if not _has_table(conn, "proof_folders"):
        return

    if not _has_column(conn, "proof_folders", "organization_id"):
        op.add_column("proof_folders", sa.Column("organization_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=True))
        op.create_index("ix_proof_folders_organization_id", "proof_folders", ["organization_id"], unique=False)

    # Replace global unique index on path with a non-unique index
    try:
        op.drop_index("ix_proof_folders_path", table_name="proof_folders")
    except Exception:
        pass
    op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=False)

    # Add composite unique constraint (path, organization_id)
    try:
        op.create_unique_constraint("uq_proof_folders_path_org", "proof_folders", ["path", "organization_id"])
    except Exception:
        pass


def downgrade() -> None:
    from alembic import context

    if context.is_offline_mode():
        op.drop_constraint("uq_proof_folders_path_org", "proof_folders", type_="unique")
        op.drop_index("ix_proof_folders_path", table_name="proof_folders")
        op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=True)
        op.drop_index("ix_proof_folders_organization_id", table_name="proof_folders")
        op.drop_column("proof_folders", "organization_id")
        return

    conn = op.get_bind()
    if not _has_table(conn, "proof_folders"):
        return

    try:
        op.drop_constraint("uq_proof_folders_path_org", "proof_folders", type_="unique")
    except Exception:
        pass

    try:
        op.drop_index("ix_proof_folders_path", table_name="proof_folders")
    except Exception:
        pass
    op.create_index("ix_proof_folders_path", "proof_folders", ["path"], unique=True)

    if _has_column(conn, "proof_folders", "organization_id"):
        try:
            op.drop_index("ix_proof_folders_organization_id", table_name="proof_folders")
        except Exception:
            pass
        op.drop_column("proof_folders", "organization_id")
