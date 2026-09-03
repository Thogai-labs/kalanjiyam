"""add condition_tags to proof_projects

Revision ID: t4u5v6w7x8y9
Revises: s3t4u5v6w7x8
"""

import sqlalchemy as sa
from alembic import op

revision = "t4u5v6w7x8y9"
down_revision = "s3t4u5v6w7x8"
branch_labels = None
depends_on = None


def _columns(table):
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    columns = _columns("proof_projects")
    if "condition_tags" not in columns:
        op.add_column(
            "proof_projects",
            sa.Column("condition_tags", sa.JSON(), nullable=True),
        )


def downgrade():
    columns = _columns("proof_projects")
    if "condition_tags" in columns:
        op.drop_column("proof_projects", "condition_tags")
