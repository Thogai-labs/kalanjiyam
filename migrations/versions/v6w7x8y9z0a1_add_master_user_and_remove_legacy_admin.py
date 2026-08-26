"""Add master_user role and remove legacy admin role.

Revision ID: v6w7x8y9z0a1
Revises: u5v6w7x8y9z0
Create Date: 2026-08-26
"""

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "v6w7x8y9z0a1"
down_revision = "u5v6w7x8y9z0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.utcnow()

    # 1. Insert master_user role if not exists
    conn.execute(
        sa.text(
            """
            INSERT INTO roles(name, created_at)
            SELECT :role_name, :created_at
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :role_name)
            """
        ),
        {"role_name": "master_user", "created_at": now},
    )

    # 2. Remove legacy admin role associations and delete admin role from roles table
    conn.execute(
        sa.text(
            """
            DELETE FROM user_roles
            WHERE role_id IN (SELECT id FROM roles WHERE name = 'admin')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM roles
            WHERE name = 'admin'
            """
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.utcnow()

    # 1. Re-insert admin role if not exists
    conn.execute(
        sa.text(
            """
            INSERT INTO roles(name, created_at)
            SELECT :role_name, :created_at
            WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :role_name)
            """
        ),
        {"role_name": "admin", "created_at": now},
    )

    # 2. Remove master_user role associations and delete master_user role
    conn.execute(
        sa.text(
            """
            DELETE FROM user_roles
            WHERE role_id IN (SELECT id FROM roles WHERE name = 'master_user')
            """
        )
    )
    conn.execute(
        sa.text(
            """
            DELETE FROM roles
            WHERE name = 'master_user'
            """
        )
    )
