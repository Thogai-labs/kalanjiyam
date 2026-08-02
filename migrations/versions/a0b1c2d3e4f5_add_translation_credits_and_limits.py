"""add translation credits and limits

Revision ID: a0b1c2d3e4f5
Revises: 9fd12345abcd
Create Date: 2026-07-08
"""

import sqlalchemy as sa
from alembic import op

revision = "a0b1c2d3e4f5"
down_revision = "9fd12345abcd"
branch_labels = None
depends_on = None


def upgrade():
    # Add translation_credits_used to users table
    op.add_column(
        "users",
        sa.Column(
            "translation_credits_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    # Add translation columns to groups table
    op.add_column(
        "groups",
        sa.Column("translation_credit_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "groups",
        sa.Column(
            "translation_credits_used",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "groups",
        sa.Column("default_user_translation_limit", sa.Integer(), nullable=True),
    )


def downgrade():
    op.drop_column("groups", "default_user_translation_limit")
    op.drop_column("groups", "translation_credits_used")
    op.drop_column("groups", "translation_credit_limit")
    op.drop_column("users", "translation_credits_used")
