"""Add organization fields, roles, and user organization.

Revision ID: b1e2f3a4c5d6
Revises: 7f3a2b1c9d0e
Create Date: 2026-05-28
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "b1e2f3a4c5d6"
down_revision = "7f3a2b1c9d0e"
branch_labels = None
depends_on = None


def _has_column(conn, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(conn).get_columns(table)}


def _has_index(conn, table: str, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspect(conn).get_indexes(table)}


def _has_fk(conn, table: str, fk_name: str) -> bool:
    for fk in inspect(conn).get_foreign_keys(table):
        if fk.get("name") == fk_name:
            return True
    return False


def upgrade() -> None:
    conn = op.get_bind()

    if not _has_column(conn, "groups", "slug"):
        op.add_column("groups", sa.Column("slug", sa.String(), nullable=True))
    if not _has_column(conn, "groups", "is_active"):
        op.add_column(
            "groups",
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        )
    if not _has_column(conn, "groups", "storage_quota_bytes"):
        op.add_column("groups", sa.Column("storage_quota_bytes", sa.BigInteger(), nullable=True))
    if not _has_column(conn, "groups", "storage_used_bytes"):
        op.add_column(
            "groups",
            sa.Column("storage_used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        )
    if not _has_column(conn, "groups", "ocr_credit_limit"):
        op.add_column("groups", sa.Column("ocr_credit_limit", sa.Integer(), nullable=True))
    if not _has_column(conn, "groups", "ocr_credits_used"):
        op.add_column(
            "groups",
            sa.Column("ocr_credits_used", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column(conn, "groups", "admin_user_id"):
        op.add_column("groups", sa.Column("admin_user_id", sa.Integer(), nullable=True))
    # SQLite cannot ADD COLUMN with DEFAULT CURRENT_TIMESTAMP; add nullable, then backfill.
    if not _has_column(conn, "groups", "created_at"):
        op.add_column("groups", sa.Column("created_at", sa.DateTime(), nullable=True))
    if not _has_column(conn, "groups", "updated_at"):
        op.add_column("groups", sa.Column("updated_at", sa.DateTime(), nullable=True))

    if conn.dialect.name != "sqlite" and not _has_fk(
        conn, "groups", "fk_groups_admin_user_id_users"
    ):
        op.create_foreign_key(
            "fk_groups_admin_user_id_users",
            "groups",
            "users",
            ["admin_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index(conn, "groups", "ix_groups_slug"):
        op.create_index(op.f("ix_groups_slug"), "groups", ["slug"], unique=True)
    if not _has_index(conn, "groups", "ix_groups_admin_user_id"):
        op.create_index(
            op.f("ix_groups_admin_user_id"), "groups", ["admin_user_id"], unique=False
        )

    if not _has_column(conn, "users", "organization_id"):
        op.add_column("users", sa.Column("organization_id", sa.Integer(), nullable=True))
    if conn.dialect.name != "sqlite" and not _has_fk(
        conn, "users", "fk_users_organization_id_groups"
    ):
        op.create_foreign_key(
            "fk_users_organization_id_groups",
            "users",
            "groups",
            ["organization_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if not _has_index(conn, "users", "ix_users_organization_id"):
        op.create_index(
            op.f("ix_users_organization_id"), "users", ["organization_id"], unique=False
        )

    now = datetime.utcnow()
    conn.execute(
        sa.text(
            "UPDATE groups SET created_at = :now, updated_at = :now WHERE created_at IS NULL"
        ),
        {"now": now},
    )

    # Backfill organization_id from the first available group membership.
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET organization_id = (
                SELECT ug.group_id
                FROM user_groups ug
                WHERE ug.user_id = users.id
                ORDER BY ug.group_id
                LIMIT 1
            )
            WHERE organization_id IS NULL
            """
        )
    )

    # Backfill deterministic slugs for existing organizations.
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

    if conn.dialect.name == "sqlite":
        with op.batch_alter_table("groups") as batch_op:
            batch_op.alter_column("slug", existing_type=sa.String(), nullable=False)
            batch_op.alter_column("created_at", existing_type=sa.DateTime(), nullable=False)
            batch_op.alter_column("updated_at", existing_type=sa.DateTime(), nullable=False)
    else:
        op.alter_column("groups", "slug", nullable=False)
        op.alter_column("groups", "created_at", nullable=False)
        op.alter_column("groups", "updated_at", nullable=False)
        op.alter_column("groups", "is_active", server_default=None)
        op.alter_column("groups", "storage_used_bytes", server_default=None)
        op.alter_column("groups", "ocr_credits_used", server_default=None)

    # Seed new roles.
    conn.execute(
        sa.text(
            """
            INSERT INTO roles(name, created_at)
            SELECT :role_name, :created_at
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :role_name)
            """
        ),
        {"role_name": "super_admin", "created_at": now},
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO roles(name, created_at)
            SELECT :role_name, :created_at
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :role_name)
            """
        ),
        {"role_name": "org_admin", "created_at": now},
    )

    # Grant super_admin to users who still have legacy admin role.
    conn.execute(
        sa.text(
            """
            INSERT INTO user_roles(user_id, role_id)
            SELECT ur.user_id, super_role.id
            FROM user_roles ur
            JOIN roles admin_role ON admin_role.id = ur.role_id AND admin_role.name = 'admin'
            JOIN roles super_role ON super_role.name = 'super_admin'
            WHERE NOT EXISTS (
                SELECT 1
                FROM user_roles ur2
                WHERE ur2.user_id = ur.user_id AND ur2.role_id = super_role.id
            )
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM roles WHERE name IN ('super_admin', 'org_admin')"))

    if _has_index(conn, "users", "ix_users_organization_id"):
        op.drop_index(op.f("ix_users_organization_id"), table_name="users")
    if conn.dialect.name != "sqlite" and _has_fk(
        conn, "users", "fk_users_organization_id_groups"
    ):
        op.drop_constraint("fk_users_organization_id_groups", "users", type_="foreignkey")
    if _has_column(conn, "users", "organization_id"):
        op.drop_column("users", "organization_id")

    if _has_index(conn, "groups", "ix_groups_admin_user_id"):
        op.drop_index(op.f("ix_groups_admin_user_id"), table_name="groups")
    if _has_index(conn, "groups", "ix_groups_slug"):
        op.drop_index(op.f("ix_groups_slug"), table_name="groups")
    if conn.dialect.name != "sqlite" and _has_fk(
        conn, "groups", "fk_groups_admin_user_id_users"
    ):
        op.drop_constraint("fk_groups_admin_user_id_users", "groups", type_="foreignkey")
    for col in (
        "updated_at",
        "created_at",
        "admin_user_id",
        "ocr_credits_used",
        "ocr_credit_limit",
        "storage_used_bytes",
        "storage_quota_bytes",
        "is_active",
        "slug",
    ):
        if _has_column(conn, "groups", col):
            op.drop_column("groups", col)
