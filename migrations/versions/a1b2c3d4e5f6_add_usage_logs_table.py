"""Add usage logs table

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "usage_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("fingerprint_id", sa.String(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("project_slug", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_usage_logs_user_id"), "usage_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_usage_logs_fingerprint_id"), "usage_logs", ["fingerprint_id"], unique=False)
    op.create_index(op.f("ix_usage_logs_ip_address"), "usage_logs", ["ip_address"], unique=False)
    op.create_index(op.f("ix_usage_logs_action"), "usage_logs", ["action"], unique=False)
    op.create_index(op.f("ix_usage_logs_project_slug"), "usage_logs", ["project_slug"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_usage_logs_project_slug"), table_name="usage_logs")
    op.drop_index(op.f("ix_usage_logs_action"), table_name="usage_logs")
    op.drop_index(op.f("ix_usage_logs_ip_address"), table_name="usage_logs")
    op.drop_index(op.f("ix_usage_logs_fingerprint_id"), table_name="usage_logs")
    op.drop_index(op.f("ix_usage_logs_user_id"), table_name="usage_logs")
    op.drop_table("usage_logs")
