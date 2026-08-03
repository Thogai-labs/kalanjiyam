"""migrate to user versions

Revision ID: 8f1145005194
Revises: a7b8c9d0e1f2
Create Date: 2026-06-29 10:38:13.788922

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = '8f1145005194'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    import datetime
    connection = op.get_bind()

    # 1. Fetch all revisions that are currently grouped under some version track.
    # We will regroup them by (page_id, author_id)
    revisions = connection.execute(
        sa.text("SELECT id, page_id, author_id, page_version_id FROM proof_revisions WHERE page_version_id IS NOT NULL")
    ).fetchall()

    # Find the current role tracks
    role_versions = connection.execute(
        sa.text("SELECT id, version_key FROM proof_page_versions WHERE version_key LIKE 'role:%'")
    ).fetchall()
    role_version_ids = {rv[0] for rv in role_versions}

    # Group revisions by (page_id, author_id)
    groups = {}
    for r in revisions:
        # Only migrate revisions that were linked to a role track
        if r[3] in role_version_ids:
            key = (r[1], r[2]) # (page_id, author_id)
            groups.setdefault(key, []).append(r[0])

    # For each group, create/get a user track and map the revisions to it
    for (page_id, author_id), rev_ids in groups.items():
        if author_id is not None:
            version_key = f"user:{author_id}"
        else:
            # Revisions with null authors (system/legacy imports) remain under a default track
            version_key = "role:p1"

        # Check if this user version track already exists for the page
        existing_id = connection.execute(
            sa.text("SELECT id FROM proof_page_versions WHERE page_id = :page_id AND version_key = :version_key"),
            {"page_id": page_id, "version_key": version_key}
        ).scalar()

        if existing_id:
            pv_id = existing_id
            # Update the version counter by adding the count of revisions
            connection.execute(
                sa.text("UPDATE proof_page_versions SET version = version + :cnt WHERE id = :id"),
                {"cnt": len(rev_ids), "id": pv_id}
            )
        else:
            # Create a new version record
            connection.execute(
                sa.text(
                    "INSERT INTO proof_page_versions (page_id, version_key, version, updated_at) "
                    "VALUES (:page_id, :version_key, :version, :updated_at)"
                ),
                {
                    "page_id": page_id,
                    "version_key": version_key,
                    "version": len(rev_ids),
                    "updated_at": datetime.datetime.utcnow(),
                }
            )
            pv_id = connection.execute(
                sa.text("SELECT id FROM proof_page_versions WHERE page_id = :page_id AND version_key = :version_key"),
                {"page_id": page_id, "version_key": version_key}
            ).scalar()

        # Update the revisions to point to the new user track in chunks to be safe
        for r_id in rev_ids:
            connection.execute(
                sa.text("UPDATE proof_revisions SET page_version_id = :pv_id WHERE id = :id"),
                {"pv_id": pv_id, "id": r_id}
            )

    # Delete the old role-based tracks that are now empty (except role:p1 if it has revisions)
    connection.execute(
        sa.text("DELETE FROM proof_page_versions WHERE version_key LIKE 'role:%' AND version_key != 'role:p1'")
    )
    # Also delete role:p1 tracks if they have no revisions left
    connection.execute(
        sa.text(
            "DELETE FROM proof_page_versions "
            "WHERE version_key = 'role:p1' "
            "AND id NOT IN (SELECT DISTINCT page_version_id FROM proof_revisions WHERE page_version_id IS NOT NULL)"
        )
    )


def downgrade() -> None:
    # Downgrading is a no-op / legacy schema remains the same.
    pass
