"""Add system_metric_logs table and trace_id column

Revision ID: a2b3c4d5e6f7
Revises: 9fd12345abcd
Create Date: 2026-07-23
"""

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if "system_metric_logs" not in tables:
        op.create_table(
            "system_metric_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("category", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("group_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Float(), nullable=True),
            sa.Column("trace_id", sa.String(), nullable=True),
            sa.Column("error_level", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("traceback", sa.Text(), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["group_id"], ["groups.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_system_metric_logs_category"), "system_metric_logs", ["category"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_name"), "system_metric_logs", ["name"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_user_id"), "system_metric_logs", ["user_id"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_group_id"), "system_metric_logs", ["group_id"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_status"), "system_metric_logs", ["status"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_trace_id"), "system_metric_logs", ["trace_id"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_error_level"), "system_metric_logs", ["error_level"], unique=False)
        op.create_index(op.f("ix_system_metric_logs_created_at"), "system_metric_logs", ["created_at"], unique=False)
    else:
        columns = [c["name"] for c in inspector.get_columns("system_metric_logs")]
        if "trace_id" not in columns:
            op.add_column("system_metric_logs", sa.Column("trace_id", sa.String(), nullable=True))
            op.create_index(op.f("ix_system_metric_logs_trace_id"), "system_metric_logs", ["trace_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_system_metric_logs_trace_id"), table_name="system_metric_logs")
    op.drop_table("system_metric_logs")
