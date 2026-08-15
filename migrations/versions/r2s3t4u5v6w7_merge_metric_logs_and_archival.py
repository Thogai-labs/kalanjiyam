"""Merge the system-metric-logs and archival-description branches.

Two heads had accumulated: `a2b3c4d5e6f7` (system metric logs) and
`q1r2s3t4u5v6` (the archival description tables and their curation layer). They
touch different tables and neither depends on the other, so the only thing
needed is a join point.

This matters operationally rather than theoretically: `deploy/prod/deploy.sh`
runs `alembic upgrade head`, which fails outright on multiple heads -- and the
script uses `set -e`, so the deploy aborts before any service starts. A merge
revision is empty by design; its whole job is to give `head` one answer.

Revision ID: r2s3t4u5v6w7
Revises: a2b3c4d5e6f7, q1r2s3t4u5v6
"""

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

revision = "r2s3t4u5v6w7"
down_revision = ("a2b3c4d5e6f7", "q1r2s3t4u5v6")
branch_labels = None
depends_on = None


def upgrade():
    """Nothing to do: this revision only joins two branches."""


def downgrade():
    """Nothing to undo -- splitting back into two heads is the absence of this."""
