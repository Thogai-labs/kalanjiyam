"""add proof page versions

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d3e1
Create Date: 2026-06-29
"""

import datetime
import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d3e1"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create proof_page_versions table
    op.create_table(
        "proof_page_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("version_key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["proof_pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("page_id", "version_key", name="uq_page_version_key")
    )

    # 2. Add page_version_id column to proof_revisions
    op.add_column("proof_revisions", sa.Column("page_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_revisions_page_version_id",
        "proof_revisions",
        "proof_page_versions",
        ["page_version_id"],
        ["id"],
        ondelete="SET NULL"
    )

    # 3. Data Migration: create 'role:p1' track for pages with revisions and link them
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT page_id, COUNT(*) as cnt FROM proof_revisions GROUP BY page_id")
    ).fetchall()

    for page_id, cnt in rows:
        # Insert a version track for 'role:p1'
        connection.execute(
            sa.text(
                "INSERT INTO proof_page_versions (page_id, version_key, version, updated_at) "
                "VALUES (:page_id, 'role:p1', :version, :now)"
            ),
            {"page_id": page_id, "version": cnt, "now": datetime.datetime.utcnow()}
        )
        
        # Get the ID of the newly inserted track
        version_id = connection.execute(
            sa.text("SELECT id FROM proof_page_versions WHERE page_id = :page_id AND version_key = 'role:p1'"),
            {"page_id": page_id}
        ).scalar()
        
        # Update revisions to link them to this track
        connection.execute(
            sa.text("UPDATE proof_revisions SET page_version_id = :version_id WHERE page_id = :page_id"),
            {"version_id": version_id, "page_id": page_id}
        )


def downgrade():
    op.drop_constraint("fk_revisions_page_version_id", "proof_revisions", type_="foreignkey")
    op.drop_column("proof_revisions", "page_version_id")
    op.drop_table("proof_page_versions")
