"""Add fingerprint_id column to projects

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-24
"""
from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("proof_projects", sa.Column("fingerprint_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_proof_projects_fingerprint_id"), "proof_projects", ["fingerprint_id"], unique=False)


def downgrade():
    op.drop_index(op.f("ix_proof_projects_fingerprint_id"), table_name="proof_projects")
    op.drop_column("proof_projects", "fingerprint_id")
