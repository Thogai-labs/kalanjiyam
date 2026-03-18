"""Add groups, user_groups, text_groups tables

Revision ID: add_groups_tables
Revises: 46107c48081d
Create Date: 2025-03-13

"""
import sqlalchemy as sa
from alembic import op


revision = "c4d5e6f7a8b9"
down_revision = "46107c48081d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_groups",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "group_id"),
    )
    op.create_index(
        op.f("ix_user_groups_group_id"), "user_groups", ["group_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_groups_user_id"), "user_groups", ["user_id"], unique=False
    )
    op.create_table(
        "text_groups",
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("text_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["text_id"], ["texts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("group_id", "text_id"),
    )
    op.create_index(
        op.f("ix_text_groups_group_id"), "text_groups", ["group_id"], unique=False
    )
    op.create_index(
        op.f("ix_text_groups_text_id"), "text_groups", ["text_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_text_groups_text_id"), table_name="text_groups")
    op.drop_index(op.f("ix_text_groups_group_id"), table_name="text_groups")
    op.drop_table("text_groups")
    op.drop_index(op.f("ix_user_groups_user_id"), table_name="user_groups")
    op.drop_index(op.f("ix_user_groups_group_id"), table_name="user_groups")
    op.drop_table("user_groups")
    op.drop_table("groups")
