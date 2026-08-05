"""add project metadata fields

Adds the bibliographic columns that the metadata tab exposes but that
`proof_projects` did not previously have, plus a JSON column holding the
extracted metadata document (languages, content analysis, derived stats,
provenance).

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-08-04
"""

import sqlalchemy as sa
from alembic import op

revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


#: Bibliographic columns. These mirror `models.proofing.string()`: non-nullable
#: with an empty-string default, so existing rows backfill cleanly.
_STRING_COLUMNS = (
    "subtitle",
    "place_of_publication",
    "edition",
    "series",
    "subject",
)


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("proof_projects")}

    for name in _STRING_COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "proof_projects",
            sa.Column(name, sa.String(), nullable=False, server_default=""),
        )

    if "extracted_metadata" not in existing:
        op.add_column(
            "proof_projects",
            sa.Column("extracted_metadata", sa.JSON(), nullable=True),
        )


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing = {c["name"] for c in inspector.get_columns("proof_projects")}

    if "extracted_metadata" in existing:
        op.drop_column("proof_projects", "extracted_metadata")
    for name in _STRING_COLUMNS:
        if name in existing:
            op.drop_column("proof_projects", name)
