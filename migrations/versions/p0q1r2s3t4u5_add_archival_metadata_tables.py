"""add archival metadata extraction tables

Creates the four tables behind the archival description pipeline: a run per
project, a window per model call, a field per tag, and an evidence span per
citation.

Every confidence column is nullable on purpose. Three of the OCR engines in
service are VLM-based and emit no confidence signal at all, so "no score" is a
legitimate state that must not be stored as 0.0 or 1.0.

Revision ID: p0q1r2s3t4u5
Revises: n8o9p0q1r2s3
Create Date: 2026-08-14
"""

import sqlalchemy as sa
from alembic import op

revision = "p0q1r2s3t4u5"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    existing = _tables()

    if "metadata_extraction_runs" not in existing:
        op.create_table(
            "metadata_extraction_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("proof_projects.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "status", sa.String(64), nullable=False, server_default="PENDING"
            ),
            sa.Column("engine", sa.String(64), nullable=True),
            sa.Column("model_name", sa.String(128), nullable=True),
            sa.Column("model_version", sa.String(64), nullable=True),
            sa.Column("taxonomy_version", sa.String(64), nullable=True),
            sa.Column("contract_version", sa.String(16), nullable=True),
            sa.Column("windows_total", sa.Integer(), nullable=True),
            sa.Column("windows_completed", sa.Integer(), nullable=True),
            sa.Column("windows_failed", sa.Integer(), nullable=True),
            sa.Column("pages_total", sa.Integer(), nullable=True),
            sa.Column("pages_read", sa.Integer(), nullable=True),
            sa.Column("fields_filled", sa.Integer(), nullable=True),
            sa.Column("fields_total", sa.Integer(), nullable=True),
            sa.Column("avg_field_confidence", sa.Float(), nullable=True),
            sa.Column("min_field_confidence", sa.Float(), nullable=True),
            sa.Column("low_conf_field_count", sa.Integer(), nullable=True),
            sa.Column("evidence_spans", sa.Integer(), nullable=True),
            sa.Column("evidence_verified", sa.Integer(), nullable=True),
            sa.Column("evidence_verified_rate", sa.Float(), nullable=True),
            sa.Column("avg_source_ocr_confidence", sa.Float(), nullable=True),
            sa.Column("pages_without_confidence", sa.Integer(), nullable=True),
            sa.Column("total_prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("total_completion_tokens", sa.Integer(), nullable=True),
            sa.Column("total_engine_latency_ms", sa.Float(), nullable=True),
            sa.Column("total_extraction_latency_ms", sa.Float(), nullable=True),
            sa.Column("metadata_data_size_bytes", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
        )

    if "metadata_windows" not in existing:
        op.create_table(
            "metadata_windows",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("metadata_extraction_runs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("window_index", sa.Integer(), nullable=False),
            sa.Column(
                "status", sa.String(64), nullable=False, server_default="PENDING"
            ),
            sa.Column(
                "attempt_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("page_slugs", sa.JSON(), nullable=True),
            sa.Column("text_hash", sa.String(64), nullable=True, index=True),
            sa.Column("fields_attempted", sa.Integer(), nullable=True),
            sa.Column("fields_returned", sa.Integer(), nullable=True),
            sa.Column("fields_declined", sa.Integer(), nullable=True),
            sa.Column("chars_in", sa.Integer(), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=True),
            sa.Column("completion_tokens", sa.Integer(), nullable=True),
            sa.Column("engine_latency_ms", sa.Float(), nullable=True),
            sa.Column("extraction_latency_ms", sa.Float(), nullable=True),
            sa.Column("avg_field_confidence", sa.Float(), nullable=True),
            sa.Column("min_field_confidence", sa.Float(), nullable=True),
            sa.Column("low_conf_field_count", sa.Integer(), nullable=True),
            sa.Column("evidence_spans", sa.Integer(), nullable=True),
            sa.Column("evidence_verified", sa.Integer(), nullable=True),
            sa.Column("source_ocr_confidence", sa.Float(), nullable=True),
            sa.Column("pages_without_confidence", sa.Integer(), nullable=True),
            sa.Column("raw_response", sa.JSON(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "run_id", "window_index", name="uq_metadata_window_index"
            ),
        )

    if "metadata_fields" not in existing:
        op.create_table(
            "metadata_fields",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "run_id",
                sa.Integer(),
                sa.ForeignKey("metadata_extraction_runs.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "project_id",
                sa.Integer(),
                sa.ForeignKey("proof_projects.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("tag_code", sa.String(64), nullable=False, index=True),
            sa.Column("value", sa.JSON(), nullable=True),
            sa.Column("curated_value", sa.JSON(), nullable=True),
            sa.Column(
                "is_curated", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("source", sa.String(32), nullable=True),
            sa.Column(
                "curated_by_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("curated_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("run_id", "tag_code", name="uq_metadata_field_tag"),
        )

    if "metadata_evidence" not in existing:
        op.create_table(
            "metadata_evidence",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "field_id",
                sa.Integer(),
                sa.ForeignKey("metadata_fields.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("value_index", sa.Integer(), nullable=True),
            sa.Column("page_slug", sa.String(255), nullable=True, index=True),
            sa.Column("block_id", sa.String(64), nullable=True),
            sa.Column("quote", sa.Text(), nullable=True),
            sa.Column("verified", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade():
    existing = _tables()
    # Children first: the foreign keys are ON DELETE CASCADE, but dropping the
    # parent table out from under them still fails on strict backends.
    for table in (
        "metadata_evidence",
        "metadata_fields",
        "metadata_windows",
        "metadata_extraction_runs",
    ):
        if table in existing:
            op.drop_table(table)
