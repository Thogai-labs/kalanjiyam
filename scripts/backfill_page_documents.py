#!/usr/bin/env python3
"""Backfill proof_revisions.document from legacy content."""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import kalanjiyam
from kalanjiyam import queries as q
from kalanjiyam.utils.page_document import PageDocument


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without writing",
    )
    args = parser.parse_args()

    app = kalanjiyam.create_app("development")
    with app.app_context():
        session = q.get_session()
        from kalanjiyam.models.proofing import Revision

        rows = session.query(Revision).filter(Revision.document.is_(None)).all()
        updated = 0
        for rev in rows:
            if not (rev.content or "").strip():
                continue
            doc = PageDocument.from_legacy_content(rev.content)
            if args.dry_run:
                updated += 1
                continue
            rev.document = doc.to_dict()
            rev.content_format = doc.content_format
            rev.content = doc.to_plain_text()
            updated += 1
        if not args.dry_run:
            session.commit()
        print(f"{'Would update' if args.dry_run else 'Updated'} {updated} revisions")


if __name__ == "__main__":
    main()
