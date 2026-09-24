"""add performance indexes for proofing

Revision ID: c7d8e9f0a1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-24 11:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _indexes(table: str) -> set[str]:
    bind = op.get_bind()
    if table not in sa.inspect(bind).get_table_names():
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    rev_indexes = _indexes("proof_revisions")
    if "ix_proof_revisions_created" not in rev_indexes:
        op.create_index("ix_proof_revisions_created", "proof_revisions", ["created"])
    if "ix_proof_revisions_project_id_created" not in rev_indexes:
        op.create_index(
            "ix_proof_revisions_project_id_created",
            "proof_revisions",
            ["project_id", "created"],
        )

    page_indexes = _indexes("proof_pages")
    if "ix_proof_pages_project_id_order" not in page_indexes:
        op.create_index(
            "ix_proof_pages_project_id_order",
            "proof_pages",
            ["project_id", "order"],
        )

    proj_indexes = _indexes("proof_projects")
    if "ix_proof_projects_created_at" not in proj_indexes:
        op.create_index(
            "ix_proof_projects_created_at", "proof_projects", ["created_at"]
        )
    if "ix_proof_projects_is_publicly_viewable" not in proj_indexes:
        op.create_index(
            "ix_proof_projects_is_publicly_viewable",
            "proof_projects",
            ["is_publicly_viewable"],
        )


def downgrade() -> None:
    proj_indexes = _indexes("proof_projects")
    if "ix_proof_projects_is_publicly_viewable" in proj_indexes:
        op.drop_index(
            "ix_proof_projects_is_publicly_viewable",
            table_name="proof_projects",
        )
    if "ix_proof_projects_created_at" in proj_indexes:
        op.drop_index("ix_proof_projects_created_at", table_name="proof_projects")

    page_indexes = _indexes("proof_pages")
    if "ix_proof_pages_project_id_order" in page_indexes:
        op.drop_index("ix_proof_pages_project_id_order", table_name="proof_pages")

    rev_indexes = _indexes("proof_revisions")
    if "ix_proof_revisions_project_id_created" in rev_indexes:
        op.drop_index(
            "ix_proof_revisions_project_id_created",
            table_name="proof_revisions",
        )
    if "ix_proof_revisions_created" in rev_indexes:
        op.drop_index("ix_proof_revisions_created", table_name="proof_revisions")
