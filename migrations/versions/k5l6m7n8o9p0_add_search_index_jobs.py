"""add search_index_jobs table

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "k5l6m7n8o9p0"
down_revision = "j4k5l6m7n8o9"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "search_index_jobs" in inspector.get_table_names():
        return

    op.create_table(
        "search_index_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(length=32), nullable=False),
        sa.Column(
            "scope_kind", sa.String(length=16), nullable=False, server_default="ALL"
        ),
        sa.Column("scope_org_id", sa.Integer(), nullable=True),
        sa.Column("scope_project_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), nullable=False, server_default="PENDING"
        ),
        sa.Column("total_docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_docs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["scope_org_id"], ["groups.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["scope_project_id"], ["proof_projects.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_index_jobs_status", "search_index_jobs", ["status"], unique=False
    )
    op.create_index(
        "ix_search_index_jobs_scope_org_id",
        "search_index_jobs",
        ["scope_org_id"],
        unique=False,
    )
    op.create_index(
        "ix_search_index_jobs_scope_project_id",
        "search_index_jobs",
        ["scope_project_id"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_search_index_jobs_scope_project_id", table_name="search_index_jobs")
    op.drop_index("ix_search_index_jobs_scope_org_id", table_name="search_index_jobs")
    op.drop_index("ix_search_index_jobs_status", table_name="search_index_jobs")
    op.drop_table("search_index_jobs")
