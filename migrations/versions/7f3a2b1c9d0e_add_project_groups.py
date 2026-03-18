"""Add project_groups table for group-based project access.

Revision ID: 7f3a2b1c9d0e
Revises: c4d5e6f7a8b9
Create Date: 2026-03-14

"""

import sqlalchemy as sa
from alembic import op


revision = "7f3a2b1c9d0e"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_groups",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["project_id"], ["proof_projects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("group_id", "project_id"),
    )
    op.create_index(
        op.f("ix_project_groups_group_id"),
        "project_groups",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_groups_project_id"),
        "project_groups",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_project_groups_project_id"), table_name="project_groups")
    op.drop_index(op.f("ix_project_groups_group_id"), table_name="project_groups")
    op.drop_table("project_groups")

