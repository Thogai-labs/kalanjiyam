"""Import PDFs from a directory into a Kalanjiyam organization.

Usage (run inside the web container):
    python /app/scripts/import_pdfs.py --pdf-dir /pdfs --org tamilsiddhabooks

The script:
  - Scans --pdf-dir for *.pdf files
  - Creates a proofing project for each PDF (title derived from filename)
  - Assigns every project to the given organization slug
  - Skips PDFs whose slugs already exist as projects
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from slugify import slugify
from sqlalchemy.orm import Session

import kalanjiyam
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.seed.utils.data_utils import create_db
from kalanjiyam.tasks.projects import create_project_inner
from kalanjiyam.tasks.utils import LocalTaskStatus


def main():
    parser = argparse.ArgumentParser(description="Bulk-import PDFs into an org.")
    parser.add_argument("--pdf-dir", required=True, help="Directory containing PDFs")
    parser.add_argument("--org", required=True, help="Organization slug")
    parser.add_argument("--username", default=None, help="Creator username (defaults to first admin)")
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists():
        print(f"ERROR: {pdf_dir} does not exist.")
        sys.exit(1)

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {pdf_dir}")
        sys.exit(0)

    app = kalanjiyam.create_app("production")
    with app.app_context():
        session = q.get_session()

        org = q.organization_by_slug(args.org)
        if org is None:
            print(f"ERROR: Organization '{args.org}' not found.")
            sys.exit(1)

        if args.username:
            creator = session.query(db.User).filter_by(username=args.username).first()
            if creator is None:
                print(f"ERROR: User '{args.username}' not found.")
                sys.exit(1)
        else:
            creator = session.query(db.User).first()
            if creator is None:
                print("ERROR: No users in database. Create a super-admin first.")
                sys.exit(1)

        upload_folder = app.config["UPLOAD_FOLDER"]

        # Creator must belong to the target org for project creation to work.
        # Commit before create_project_inner so its nested session sees the change.
        if creator.organization_id != org.id:
            print(f"  INFO  Assigning '{creator.username}' to org '{org.name}' for import.")
            creator.organization_id = org.id
            session.add(creator)
            session.commit()

        print(f"Importing {len(pdfs)} PDFs into org '{org.name}' as '{creator.username}'")
        print("-" * 60)

        for pdf in pdfs:
            title = pdf.stem.replace("_", " ").replace("-", " ").title()
            slug = slugify(title)

            existing = session.query(db.Project).filter_by(slug=slug).first()
            if existing:
                print(f"  SKIP  {pdf.name} → project '{slug}' already exists")
                continue

            page_image_dir = Path(upload_folder) / "projects" / slug / "pages"
            page_image_dir.mkdir(parents=True, exist_ok=True)

            try:
                create_project_inner(
                    display_title=title,
                    pdf_path=str(pdf),
                    output_dir=str(page_image_dir),
                    app_environment=app.config["KALANJIYAM_ENVIRONMENT"],
                    creator_id=creator.id,
                    task_status=LocalTaskStatus(),
                )
                project = session.query(db.Project).filter_by(slug=slug).first()
                if project:
                    q.add_project_to_group(project_id=project.id, group_id=org.id)
                    print(f"  OK    {pdf.name} → '{title}' ({len(project.pages)} pages)")
                else:
                    print(f"  WARN  {pdf.name} → created but couldn't find project record")
            except Exception as e:
                print(f"  FAIL  {pdf.name} → {e}")

        session.commit()
        print("-" * 60)
        print("Done.")


if __name__ == "__main__":
    main()
