"""Enforce NOT NULL on groups.slug and timestamps (SQLite fix).

Revision ID: c2d3e4f5a6b7
Revises: b1e2f3a4c5d6
Create Date: 2026-05-28

The prior migration backfills these columns but skips ALTER ... NOT NULL on SQLite.
This revision aligns the SQLite schema with the SQLAlchemy models.
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op


revision = "c2d3e4f5a6b7"
down_revision = "b1e2f3a4c5d6"
branch_labels = None
depends_on = None


def _backfill_groups(conn) -> None:
    now = datetime.utcnow()
    conn.execute(
        sa.text(
            "UPDATE groups SET created_at = :now, updated_at = :now "
            "WHERE created_at IS NULL OR updated_at IS NULL"
        ),
        {"now": now},
    )
    rows = conn.execute(sa.text("SELECT id, name, slug FROM groups ORDER BY id")).fetchall()
    used = {slug for _, _, slug in rows if slug}
    for group_id, name, existing_slug in rows:
        if existing_slug:
            continue
        base = (name or f"org-{group_id}").strip().lower().replace(" ", "-")
        base = "".join(ch for ch in base if ch.isalnum() or ch == "-").strip("-") or f"org-{group_id}"
        slug = base
        i = 2
        while slug in used:
            slug = f"{base}-{i}"
            i += 1
        used.add(slug)
        conn.execute(
            sa.text("UPDATE groups SET slug = :slug WHERE id = :id"),
            {"slug": slug, "id": group_id},
        )


def upgrade() -> None:
    conn = op.get_bind()
    _backfill_groups(conn)

    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("groups") as batch_op:
            batch_op.alter_column("slug", existing_type=sa.String(), nullable=False)
            batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)
            batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=False)
    else:
        op.alter_column("groups", "slug", nullable=False)
        op.alter_column("groups", "created_at", nullable=False)
        op.alter_column("groups", "updated_at", nullable=False)


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("groups") as batch_op:
            batch_op.alter_column("slug", existing_type=sa.String(), nullable=True)
            batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=True)
            batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=True)
    else:
        op.alter_column("groups", "slug", nullable=True)
        op.alter_column("groups", "created_at", nullable=True)
        op.alter_column("groups", "updated_at", nullable=True)
