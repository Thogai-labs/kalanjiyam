"""allow curated description fields to exist without an extraction run

`metadata_fields.run_id` becomes nullable so that a row can hold what an
archivist typed rather than what a run produced. Three tags (REFERENCE,
CUSTODIAL HISTORY, ACCESS) are write-locked against the extractor and can only
ever be entered by hand -- requiring a run to hang them from would make them
unenterable on a project nobody has extracted yet.

A NULL never collides in a UNIQUE constraint, so the existing
(run_id, tag_code) constraint cannot keep the curated layer unique. A partial
unique index on (project_id, tag_code) does that job for the NULL rows only,
leaving generated rows free to repeat a tag across runs.

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None

_INDEX = "uq_metadata_field_curated"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table() -> bool:
    return "metadata_fields" in set(_inspector().get_table_names())


def _run_id_is_nullable() -> bool:
    for column in _inspector().get_columns("metadata_fields"):
        if column["name"] == "run_id":
            return bool(column["nullable"])
    return False


def _has_index() -> bool:
    return _INDEX in {i["name"] for i in _inspector().get_indexes("metadata_fields")}


def upgrade():
    # The table is created one revision earlier, but a partially-applied history
    # should not crash the rest of the upgrade.
    if not _has_table():
        return

    if not _run_id_is_nullable():
        # batch_alter_table: SQLite cannot ALTER a column in place and needs the
        # copy-and-rename dance, which this generates.
        with op.batch_alter_table("metadata_fields") as batch:
            batch.alter_column("run_id", existing_type=sa.Integer(), nullable=True)

    if not _has_index():
        op.create_index(
            _INDEX,
            "metadata_fields",
            ["project_id", "tag_code"],
            unique=True,
            sqlite_where=sa.text("run_id IS NULL"),
            postgresql_where=sa.text("run_id IS NULL"),
        )


def downgrade():
    if not _has_table():
        return

    if _has_index():
        op.drop_index(_INDEX, table_name="metadata_fields")

    # Curated rows have no run to belong to, so they cannot survive the column
    # becoming NOT NULL. Drop them rather than inventing a run for them.
    op.execute(sa.text("DELETE FROM metadata_fields WHERE run_id IS NULL"))

    if _run_id_is_nullable():
        with op.batch_alter_table("metadata_fields") as batch:
            batch.alter_column("run_id", existing_type=sa.Integer(), nullable=False)
