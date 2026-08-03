"""make revision author nullable

Revision ID: e5f6a7b8c9d2
Revises: e5f6a7b8c9d1
Create Date: 2026-06-25
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d2"
down_revision = "e5f6a7b8c9d1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("proof_revisions", schema=None) as batch_op:
        batch_op.alter_column("author_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table("proof_revisions", schema=None) as batch_op:
        batch_op.alter_column("author_id", existing_type=sa.Integer(), nullable=False)
