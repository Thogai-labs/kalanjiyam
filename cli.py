#!/usr/bin/env python3

import getpass

import click
from datetime import datetime
from slugify import slugify
from sqlalchemy import or_
from sqlalchemy.orm import Session

import kalanjiyam
from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.seed.utils.data_utils import create_db
from kalanjiyam.tasks.projects import create_project_inner
from kalanjiyam.tasks.utils import LocalTaskStatus

engine = create_db()


@click.group()
def cli():
    pass


@cli.command()
def create_user():
    """Create a new user.

    This command is best used in development to quickly create new users.
    """
    username = input("Username: ")
    raw_password = getpass.getpass("Password: ")
    email = input("Email: ")

    with Session(engine) as session:
        u = (
            session.query(db.User)
            .where(or_(db.User.username == username, db.User.email == email))
            .first()
        )
        if u is not None:
            if u.username == username:
                raise click.ClickException(f'User "{username}" already exists.')
            else:
                raise click.ClickException(f'Email "{email}" already exists.')

        user = db.User(username=username, email=email)
        user.set_password(raw_password)
        session.add(user)
        session.commit()


@cli.command()
@click.option("--username", help="the user to modify")
@click.option("--role", help="the role to add")
def add_role(username, role):
    """Add the given role to the given user.

    In particular, `add-role <user> admin` will give a user administrator
    privileges and grant them full access to Kalanjiyam's data and content.
    """
    with Session(engine) as session:
        u = session.query(db.User).where(db.User.username == username).first()
        if u is None:
            raise click.ClickException(f'User "{username}" does not exist.')
        r = session.query(db.Role).where(db.Role.name == role).first()
        if r is None:
            raise click.ClickException(f'Role "{role}" does not exist.')
        if role == SiteRole.ADMIN.value:
            click.echo(
                "Warning: `admin` is deprecated for production. Use `super_admin` instead."
            )
        if role == SiteRole.SUPER_ADMIN.value:
            raise click.ClickException(
                'Use `create-super-admin` to grant super_admin (not add-role).'
            )
        if r in u.roles:
            raise click.ClickException(f'User "{username}" already has role "{role}".')

        u.roles.append(r)
        session.add(u)
        session.commit()
    print(f'Added role "{role}" to user "{username}".')


@cli.command()
@click.option("--username", help="the user whose password to change")
def change_password(username):
    """Change a user's password.
    
    This command prompts for a new password and confirmation.
    """
    if not username:
        username = input("Username: ")
    
    new_password = getpass.getpass("New password: ")
    confirm_password = getpass.getpass("Confirm password: ")
    
    if new_password != confirm_password:
        raise click.ClickException("Passwords don't match.")
    
    if not new_password.strip():
        raise click.ClickException("Password cannot be empty.")
    
    with Session(engine) as session:
        u = session.query(db.User).where(db.User.username == username).first()
        if u is None:
            raise click.ClickException(f'User "{username}" does not exist.')
        
        u.set_password(new_password)
        session.add(u)
        session.commit()
    
    print(f'Changed password for user "{username}".')


@cli.command()
@click.option("--title", help="title of the new project")
@click.option("--pdf-path", help="path to the source PDF")
def create_project(title, pdf_path):
    """Create a proofing project from a PDF."""
    current_app = kalanjiyam.create_app("development")
    with current_app.app_context():
        session = q.get_session()
        arbitrary_user = session.query(db.User).first()
        if not arbitrary_user:
            raise click.ClickException(
                "Every project must have a user that created it. "
                "But, no users were found in the database.\n"
                "Please create a user first with `create-user`."
            )

        from kalanjiyam.utils.storage import get_storage, pdf_key

        slug = slugify(title)
        source_pdf_key = pdf_key(slug)
        get_storage().save(source_pdf_key, pdf_path)
        create_project_inner(
            display_title=title,
            pdf_key=source_pdf_key,
            app_environment=current_app.config["KALANJIYAM_ENVIRONMENT"],
            creator_id=arbitrary_user.id,
            task_status=LocalTaskStatus(),
        )


def _get_role(session: Session, role_name: str):
    role = session.query(db.Role).where(db.Role.name == role_name).first()
    if role is None:
        raise click.ClickException(f'Role "{role_name}" does not exist.')
    return role


@cli.command()
def create_super_admin():
    """Create the platform super-admin user (CLI-only; only one allowed)."""
    username = input("Username: ")
    email = input("Email: ")
    raw_password = getpass.getpass("Password: ")

    with Session(engine) as session:
        from kalanjiyam.admin_user import count_super_admins

        if count_super_admins(session) >= 1:
            raise click.ClickException(
                "A super admin already exists. Only one is allowed. "
                "Use ./cli.py change-password to update that account."
            )

        existing = (
            session.query(db.User)
            .where(or_(db.User.username == username, db.User.email == email))
            .first()
        )
        if existing is not None:
            raise click.ClickException("User with this username/email already exists.")

        user = db.User(username=username, email=email)
        user.set_password(raw_password)
        session.add(user)
        session.flush()

        role = _get_role(session, SiteRole.SUPER_ADMIN.value)
        user.roles.append(role)
        session.add(user)
        session.commit()
    click.echo(f'Created super admin "{username}".')


@cli.command()
@click.option("--name", prompt=True, help="Organization name")
@click.option("--slug", prompt=True, help="Organization slug")
@click.option("--description", default="", help="Organization description")
def create_organization(name, slug, description):
    """Create an organization/group."""
    with Session(engine) as session:
        if session.query(db.Group).filter_by(slug=slug).first():
            raise click.ClickException(f'Organization "{slug}" already exists.')
        org = db.Group(name=name, slug=slug, description=description)
        session.add(org)
        session.commit()
    click.echo(f'Created organization "{slug}".')


@cli.command()
@click.option("--org", "org_slug", required=True, help="Organization slug")
@click.option("--username", required=True)
@click.option("--email", required=False)
def assign_org_admin(org_slug, username, email):
    """Assign org admin role to a user in an organization."""
    with Session(engine) as session:
        org = session.query(db.Group).filter_by(slug=org_slug).first()
        if org is None:
            raise click.ClickException(f'Organization "{org_slug}" does not exist.')

        user = session.query(db.User).filter_by(username=username).first()
        if user is None:
            if not email:
                raise click.ClickException("Provide --email when creating a new user.")
            raw_password = getpass.getpass("Password: ")
            user = db.User(username=username, email=email)
            user.set_password(raw_password)
            session.add(user)
            session.flush()

        org_admin_role = _get_role(session, SiteRole.ORG_ADMIN.value)
        if org_admin_role not in user.roles:
            user.roles.append(org_admin_role)
        user.organization_id = org.id
        org.admin_user_id = user.id
        session.query(db.UserGroups).filter_by(user_id=user.id).delete()
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
        session.add_all([user, org])
        session.commit()
    click.echo(f'Assigned "{username}" as org admin for "{org_slug}".')


@cli.command()
@click.option("--org", "org_slug", required=True, help="Organization slug")
@click.option("--username", required=True)
@click.option("--email", required=True)
def create_org_user(org_slug, username, email):
    """Create an organization user with default P1 role."""
    raw_password = getpass.getpass("Password: ")
    with Session(engine) as session:
        org = session.query(db.Group).filter_by(slug=org_slug).first()
        if org is None:
            raise click.ClickException(f'Organization "{org_slug}" does not exist.')
        if session.query(db.User).filter_by(username=username).first():
            raise click.ClickException(f'User "{username}" already exists.')

        user = db.User(username=username, email=email, organization_id=org.id)
        user.set_password(raw_password)
        user.roles.append(_get_role(session, SiteRole.P1.value))
        session.add(user)
        session.flush()
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
        session.commit()
    click.echo(f'Created org user "{username}" in "{org_slug}".')


@cli.command()
@click.option("--org", "org_slug", required=True, help="Organization slug")
@click.option("--storage-mb", type=int, required=False)
@click.option("--ocr-credits", type=int, required=False)
def set_org_quota(org_slug, storage_mb, ocr_credits):
    """Set organization storage and OCR quotas."""
    with Session(engine) as session:
        org = session.query(db.Group).filter_by(slug=org_slug).first()
        if org is None:
            raise click.ClickException(f'Organization "{org_slug}" does not exist.')
        if storage_mb is not None:
            org.storage_quota_bytes = int(storage_mb) * 1024 * 1024
        if ocr_credits is not None:
            org.ocr_credit_limit = int(ocr_credits)
        session.add(org)
        session.commit()
    click.echo(f'Updated quotas for "{org_slug}".')


@cli.command()
@click.option("--days", type=int, default=7, help="Delete files older than N days (default: 7)")
@click.option("--force", is_flag=True, help="Force cleanup even if AUTO_UPLOADED_FILES_CLEANUP is false")
@click.option("--env", "app_env", default="development", help="Application environment (default: development)")
def cleanup_uploads(days, force, app_env):
    """Delete uploaded source PDF and DOC/DOCX files older than specified days."""
    from config import create_config_only_app
    from kalanjiyam.utils.storage import cleanup_old_uploaded_files, get_storage

    app = create_config_only_app(app_env)
    with app.app_context():
        enabled = app.config.get("AUTO_UPLOADED_FILES_CLEANUP", False)
        if not enabled and not force:
            click.echo("AUTO_UPLOADED_FILES_CLEANUP is disabled. Pass --force to override.")
            return
        storage = get_storage()
        deleted = cleanup_old_uploaded_files(storage, days=days)
        click.echo(f"Cleaned up {deleted} uploaded source PDF/DOC files older than {days} days.")


@cli.command()
@click.option("--s3-uri", required=False, help="S3 URI target (e.g., s3://bucket/test/)")
@click.option("--local-uri", required=False, help="Local path target (e.g., /home/user/test/)")
@click.option("--org", required=False, help="Organization slug to attach the processed projects to (e.g., 'udaan')")
@click.option("--pdf", is_flag=True, help="Process PDF files only")
@click.option("--image", is_flag=True, help="Process image directories only")
@click.option("--lang", "--language", "lang", default="en", help="OCR Language code (default: 'en', e.g. 'en', 'ta', 'hi')")
@click.option("--engine", "ocr_engine", default="surya", help="OCR Engine (e.g. 'surya', 'google', 'deepseek', '1', '3')")
@click.option("--extract-metadata", is_flag=True, default=False, help="Automatically run archival metadata extraction once OCR completes for each book")
def batch_ocr(s3_uri, local_uri, org, pdf, image, lang, ocr_engine, extract_metadata):
    """Start a Batch OCR process for PDFs and Image folders from S3 or Local."""
    import boto3
    import os
    from urllib.parse import urlparse
    from kalanjiyam.models.batch import BatchJob, BatchItem
    from kalanjiyam.tasks.s3_batch import process_s3_batch_item
    from kalanjiyam.utils.ocr_types import normalize_engine
    import mimetypes
    
    norm_engine = normalize_engine(ocr_engine)
    
    if not s3_uri and not local_uri:
        raise click.UsageError("You must provide either --s3-uri or --local-uri")
        
    if s3_uri and local_uri:
        raise click.UsageError("You cannot provide both --s3-uri and --local-uri")
        
    if org:
        org = slugify(org)
        from kalanjiyam.models.group import Group
        with Session(engine) as session:
            if not session.query(Group).filter_by(slug=org).first():
                raise click.ClickException(f"Organization '{org}' not found. Please provide a valid organization slug.")
    
    # Logic for filtering
    process_pdfs = pdf or (not pdf and not image)
    process_images = image or (not pdf and not image)
    
    items_to_process = []
    image_groups = {}
    
    target_uri = s3_uri or local_uri
    
    if s3_uri:
        parsed_uri = urlparse(s3_uri)
        if parsed_uri.scheme != 's3':
            raise click.ClickException("Target must be an S3 URI (s3://...)")
            
        bucket_name = parsed_uri.netloc
        prefix = parsed_uri.path.lstrip('/')
        
        click.echo(f"Scanning S3 s3://{bucket_name}/{prefix} recursively...")
        
        s3_client = boto3.client('s3')
        paginator = s3_client.get_paginator('list_objects_v2')
        
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                # Skip folders
                if key.endswith('/'):
                    continue
                    
                mime_type, _ = mimetypes.guess_type(key)
                if not mime_type:
                    if key.lower().endswith('.pdf'):
                        mime_type = 'application/pdf'
                    else:
                        continue
                    
                if mime_type == 'application/pdf' and process_pdfs:
                    items_to_process.append({
                        'path': f"s3://{bucket_name}/{key}",
                        'mime_type': mime_type,
                        'type': 'pdf'
                    })
                elif mime_type.startswith('image/') and process_images:
                    parent_prefix = os.path.dirname(key)
                    if parent_prefix not in image_groups:
                        image_groups[parent_prefix] = []
                    image_groups[parent_prefix].append(key)
                    
        if process_images:
            for parent_prefix, keys in image_groups.items():
                items_to_process.append({
                    'path': f"s3://{bucket_name}/{parent_prefix}",
                    'mime_type': 'image_folder',
                    'type': 'image_folder',
                    'count': len(keys)
                })
                
    elif local_uri:
        # Local processing
        local_path = os.path.abspath(local_uri)
        if not os.path.exists(local_path):
            raise click.ClickException(f"Local path '{local_path}' does not exist.")
            
        click.echo(f"Scanning local path {local_path} recursively...")
        
        for root, dirs, files in os.walk(local_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Convert Windows paths to forward slashes for consistency if needed, but not strictly necessary for file://
                file_path_clean = file_path.replace('\\', '/')
                mime_type, _ = mimetypes.guess_type(file_path_clean)
                
                if not mime_type:
                    if file_path_clean.lower().endswith('.pdf'):
                        mime_type = 'application/pdf'
                    else:
                        continue
                    
                if mime_type == 'application/pdf' and process_pdfs:
                    items_to_process.append({
                        'path': f"file://{file_path_clean}",
                        'mime_type': mime_type,
                        'type': 'pdf'
                    })
                elif mime_type.startswith('image/') and process_images:
                    parent_path = root.replace('\\', '/')
                    if parent_path not in image_groups:
                        image_groups[parent_path] = []
                    image_groups[parent_path].append(file_path_clean)
                    
        if process_images:
            for parent_path, image_keys in image_groups.items():
                items_to_process.append({
                    'path': f"file://{parent_path}",
                    'mime_type': 'image_folder',
                    'type': 'image_folder',
                    'count': len(image_keys)
                })
            
    if not items_to_process:
        click.echo("No matching files found. Exiting.")
        return
        
    with Session(engine) as session:
        job = BatchJob(target_uri=target_uri, status='PENDING', extract_metadata=extract_metadata)
        session.add(job)
        session.flush()
        
        click.echo(f"Created BatchJob ID: {job.id}. Dispatching items...")
        
        db_items = []
        for item_data in items_to_process:
            item = BatchItem(
                job_id=job.id,
                file_path=item_data['path'],
                mime_type=item_data['mime_type'],
                engine=norm_engine,
                status='PENDING'
            )
            session.add(item)
            session.flush()
            db_items.append(item)
            
        # Commit items to DB so Celery can find them!
        session.commit()
        
        for item in db_items:
            # Dispatch to Celery
            process_s3_batch_item.apply_async(
                args=[item.id, org, lang],
                kwargs={"engine": norm_engine},
                queue='s3_batch'
            )
            
        click.echo(f"Dispatched {len(db_items)} items to the s3_batch Celery queue successfully!")


@cli.command("import-jsonl")
@click.option("--jsonl-uri", required=True, help="S3 URI (s3://...) or local directory path for JSONL files")
@click.option("--pdf-uri", required=True, help="S3 URI (s3://...) or local directory path for PDF files")
@click.option("--org", required=True, help="Organization slug for imported projects")
@click.option("--dry-run", is_flag=True, help="Discover and validate without writing DB or storage")
@click.option(
    "--allow-duplicate",
    default=False,
    type=bool,
    help="Allow importing books that already exist in the organization (default: false)",
)
def import_jsonl(jsonl_uri, pdf_uri, org, dry_run, allow_duplicate):
    """Import PDF pages and JSONL OCR records from S3 or local filesystem (JSONL page numbers are 1-based)."""
    import os
    from kalanjiyam.models.group import Group
    from kalanjiyam.services.jsonl_import import ImportValidationError, run_import

    env_name = os.environ.get("FLASK_ENV") or os.environ.get("KALANJIYAM_ENVIRONMENT") or "development"
    app = kalanjiyam.create_app(env_name)

    org = slugify(org)
    with app.app_context():
        with Session(engine) as session:
            if not session.query(Group).filter_by(slug=org).first():
                raise click.ClickException(f"Organization '{org}' not found.")
            try:
                summary = run_import(
                    session,
                    jsonl_uri=jsonl_uri,
                    pdf_uri=pdf_uri,
                    org_slug=org,
                    dry_run=dry_run,
                    allow_duplicate=allow_duplicate,
                )
            except ImportValidationError as exc:
                raise click.ClickException(str(exc)) from exc
    click.echo(f"JSONL files discovered: {summary.jsonl_files}")
    click.echo(f"Books discovered: {summary.books}")
    click.echo(f"Pages discovered: {summary.pages}")
    click.echo(f"PDFs matched: {summary.matched_pdfs}")
    click.echo(f"PDFs missing: {summary.missing_pdfs}")
    if summary.ambiguous_pdfs > 0:
        click.echo(f"PDFs ambiguous (multiple matches): {summary.ambiguous_pdfs}")
    click.echo(f"Duplicate pages: {summary.duplicate_pages}")
    click.echo(f"Malformed records: {summary.malformed_records}")
    click.echo(f"Skipped books (duplicates): {summary.skipped_books}")
    click.echo(f"Importable books: {summary.importable_books}")
    click.echo(f"Invalid books: {summary.invalid_books}")
    if dry_run:
        click.echo("Dry run: no database or storage writes performed.")


@cli.command("metadata-extract")
@click.option("--project", "slug", required=True, help="Project slug to describe")
@click.option(
    "--force",
    is_flag=True,
    help="Re-ask every window, even ones whose text has not changed since the last run",
)
@click.option(
    "--sync",
    is_flag=True,
    help="Run in this process instead of queueing to Celery (useful for a first look)",
)
def metadata_extract(slug, force, sync):
    """Extract the archival description for a project, reading every page.

    Unlike `metadata` sampling, this is a full pass: fifteen of the twenty-two
    tags in the schema ask who and what is named *anywhere* in the file, which a
    sample cannot answer.
    """
    from kalanjiyam.tasks import archival_extract

    session = Session(engine)
    project = session.query(db.Project).filter_by(slug=slug).first()
    if project is None:
        raise click.ClickException(f"project {slug!r} not found")

    if not sync:
        task = archival_extract.extract_archival_metadata.delay(project.id, force=force)
        click.echo(f"Queued extraction for {slug} (task {task.id}) on the metadata queue.")
        click.echo("Follow it with: python cli.py metadata-status --project " + slug)
        return

    click.echo(f"Extracting {slug} in this process; this reads every page.")
    result = archival_extract.extract_archival_metadata(project.id, force=force)
    status = result.get("status")
    click.echo(f"Status: {status}")
    if result.get("error"):
        raise click.ClickException(result["error"])

    click.echo(
        f"Windows: {result.get('windows_completed')} completed, "
        f"{result.get('windows_failed')} failed"
    )
    click.echo(f"Fields filled: {result.get('fields_filled')} of 22")
    rate = result.get("evidence_verified_rate")
    click.echo(
        "Evidence verified: "
        + ("no verifiable spans" if rate is None else f"{rate:.1%}")
    )


@cli.command("metadata-status")
@click.option("--project", "slug", required=True, help="Project slug")
def metadata_status(slug):
    """Show the latest archival extraction run for a project."""
    from kalanjiyam.tasks import archival_extract

    session = Session(engine)
    project = session.query(db.Project).filter_by(slug=slug).first()
    if project is None:
        raise click.ClickException(f"project {slug!r} not found")

    progress = archival_extract.get_progress(project.id)
    if progress:
        click.echo(
            f"In flight: {progress.get('stage')} "
            f"({progress.get('done')}/{progress.get('total')}) "
            f"status={progress.get('status')}"
        )

    run = (
        session.query(db.MetadataExtractionRun)
        .filter_by(project_id=project.id)
        .order_by(db.MetadataExtractionRun.id.desc())
        .first()
    )
    if run is None:
        click.echo("No extraction run recorded yet.")
        return

    def pct(value):
        return "n/a" if value is None else f"{value:.1%}"

    click.echo(f"Run {run.id}: {run.status}  ({run.created_at})")
    click.echo(f"  Engine/model:        {run.engine or '?'} {run.model_name or ''}")
    click.echo(f"  Taxonomy:            {run.taxonomy_version}")
    click.echo(
        f"  Windows:             {run.windows_completed}/{run.windows_total} "
        f"({run.windows_failed} failed)"
    )
    click.echo(
        f"  Extraction coverage: {pct(run.extraction_coverage)} "
        f"({run.pages_read}/{run.pages_total} pages)"
    )
    click.echo(
        f"  Field coverage:      {pct(run.field_coverage)} "
        f"({run.fields_filled}/{run.fields_total} tags)"
    )
    click.echo(f"  Evidence verified:   {pct(run.evidence_verified_rate)}")
    # An average over a mix of measured and missing scores would be a lie, so
    # say plainly when there was nothing to average.
    if run.avg_source_ocr_confidence is None:
        click.echo("  Source OCR conf.:    none reported (confidence-blind engine)")
    else:
        click.echo(
            f"  Source OCR conf.:    {run.avg_source_ocr_confidence:.3f} "
            f"({run.pages_without_confidence or 0} pages without a score)"
        )
    click.echo(
        f"  Tokens:              {(run.total_prompt_tokens or 0) + (run.total_completion_tokens or 0)}"
    )


def _format_duration(seconds):
    if seconds is None or seconds < 0:
        return "N/A"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"


@cli.command()
@click.option("--limit", default=20, type=int, help="Number of recent jobs to list")
def batch_list(limit):
    """List recent batch jobs with their status, created time, and duration."""
    from kalanjiyam.models.batch import BatchJob
    
    with Session(engine) as session:
        jobs = session.query(BatchJob).order_by(BatchJob.id.desc()).limit(limit).all()

        if not jobs:
            click.echo("No batch jobs found in database.")
            return
            
        click.echo(f"{'ID':<5} | {'Status':<15} | {'Created At':<22} | {'Time Taken':<12} | Target")
        click.echo("-" * 95)
        for j in jobs:
            created_str = j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "Unknown"
            if j.completed_at and j.created_at:
                dur = _format_duration((j.completed_at - j.created_at).total_seconds())
            elif j.created_at and j.status in ('PENDING', 'IN_PROGRESS'):
                dur = _format_duration((datetime.utcnow() - j.created_at).total_seconds())
            else:
                dur = "N/A"
            click.echo(f"{j.id:<5} | {j.status:<15} | {created_str:<22} | {dur:<12} | {j.target_uri}")


@cli.command()
@click.option("--job-id", required=True, type=int, help="Batch Job ID to cancel")
def batch_cancel(job_id):
    """Cancel a pending or in-progress Batch OCR job."""
    from kalanjiyam.models.batch import BatchJob
    
    with Session(engine) as session:
        job = session.query(BatchJob).get(job_id)
        if not job:
            raise click.ClickException(f"BatchJob ID {job_id} not found.")
            
        if job.status in ('COMPLETED', 'FAILED'):
            click.echo(f"Job {job.id} is already {job.status}.")
            return
            
        job.status = 'FAILED'
        job.completed_at = datetime.utcnow()
        job.error_message = 'Cancelled by user'
        
        cancelled_count = 0
        for item in job.items:
            if item.status in ('PENDING', 'IN_PROGRESS', 'DOWNLOADED', 'IMAGES_EXTRACTED', 'OCR_IN_PROGRESS'):
                item.status = 'FAILED'
                item.error_message = 'Cancelled by user'
                cancelled_count += 1

                for chunk in item.chunks:
                    if chunk.status in ('PENDING', 'IN_PROGRESS'):
                        chunk.status = 'FAILED'
                        chunk.error_message = 'Cancelled by user'
                        chunk.completed_at = datetime.utcnow()

                for ocr_p in item.ocr_pages:
                    if ocr_p.status in ('PENDING', 'IN_PROGRESS'):
                        ocr_p.status = 'FAILED'
                        ocr_p.error_message = 'Cancelled by user'
                        ocr_p.completed_at = datetime.utcnow()
                
        session.commit()
        click.echo(f"Successfully cancelled BatchJob {job.id}. {cancelled_count} pending/active items marked as failed.")


@cli.command()
@click.option("--job-id", required=True, type=int, help="Batch Job ID to retry")
@click.option("--org", required=False, help="Organization slug to attach the processed projects to")
@click.option("--lang", "--language", "lang", default="en", help="OCR Language code (default: 'en')")
@click.option("--engine", "ocr_engine", default=None, help="OCR engine name or masked ID (e.g. 'surya', 'dots_ocr', '12')")
@click.option("--force", is_flag=True, help="Force rerun OCR on ALL pages including already COMPLETED ones")
@click.option("--extract-metadata/--no-extract-metadata", default=None, help="Enable or disable archival metadata extraction on completion")
def batch_retry(job_id, org, lang, ocr_engine, force, extract_metadata):
    """Retry failed or stuck items/chunks in a Batch OCR job without re-scanning.

    Use --force to re-run OCR on all pages (including completed ones)
    without deleting projects.
    """
    from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrChunk, BatchOcrPage
    from kalanjiyam.tasks.s3_batch import process_s3_batch_item, process_s3_batch_chunk
    from kalanjiyam.utils.ocr_types import normalize_engine as _norm_eng

    resolved_engine = _norm_eng(ocr_engine) if ocr_engine else None

    if org:
        org = slugify(org)
        from kalanjiyam.models.group import Group
        with Session(engine) as session:
            if not session.query(Group).filter_by(slug=org).first():
                raise click.ClickException(f"Organization '{org}' not found. Please provide a valid organization slug.")
                
    with Session(engine) as session:
        job = session.query(BatchJob).get(job_id)
        if not job:
            raise click.ClickException(f"BatchJob ID {job_id} not found.")

        if force:
            # --force: retry everything, including completed items
            items_to_retry = list(job.items)
        else:
            items_to_retry = [
                item for item in job.items
                if item.status in ('FAILED', 'PENDING', 'IN_PROGRESS', 'IMAGES_EXTRACTED')
                or any(page.status == 'FAILED' for page in item.ocr_pages)
            ]
        
        if not items_to_retry:
            click.echo(f"Job #{job_id} has no items to retry. Use --force to rerun completed pages.")
            return
            
        job.status = 'IN_PROGRESS'
        job.error_message = None
        job.completed_at = None
        if extract_metadata is not None:
            job.extract_metadata = extract_metadata

        chunks_dispatched = 0
        items_dispatched = 0
        
        for item in items_to_retry:
            # Override engine if specified
            if resolved_engine:
                item.engine = resolved_engine

            if force:
                for ocr_p in item.ocr_pages:
                    ocr_p.status = 'PENDING'
                    ocr_p.error_message = None
                    ocr_p.completed_at = None

            if item.project_id and item.chunks:
                item.status = 'IN_PROGRESS'
                item.error_message = None
                item.completed_at = None
                
                for chunk in item.chunks:
                    has_failed_pages = any(page.status == 'FAILED' for page in chunk.pages)
                    if force or chunk.status in ('FAILED', 'PENDING', 'IN_PROGRESS') or has_failed_pages:
                        chunk.status = 'PENDING'
                        chunk.error_message = None
                        chunk.completed_at = None
                        
                        for ocr_p in chunk.pages:
                            if force or ocr_p.status != 'COMPLETED':
                                ocr_p.status = 'PENDING'
                                ocr_p.error_message = None
                                ocr_p.completed_at = None
                                
                        session.commit()
                        dispatch_kwargs = {}
                        if item.engine:
                            dispatch_kwargs["engine"] = item.engine
                        if force:
                            dispatch_kwargs["force"] = True
                        process_s3_batch_chunk.apply_async(
                            args=[chunk.id, org, lang],
                            kwargs=dispatch_kwargs,
                            queue='s3_batch'
                        )
                        chunks_dispatched += 1
            else:
                item.status = 'PENDING'
                item.error_message = None
                session.commit()
                dispatch_kwargs = {}
                if item.engine:
                    dispatch_kwargs["engine"] = item.engine
                if force:
                    dispatch_kwargs["force"] = True
                process_s3_batch_item.apply_async(
                    args=[item.id, org, lang],
                    kwargs=dispatch_kwargs,
                    queue='s3_batch'
                )
                items_dispatched += 1
            
        session.commit()
        mode = "FORCE rerun (all pages)" if force else "retry (failed only)"
        click.echo(f"[{mode}] Re-dispatched {chunks_dispatched} chunk tasks and {items_dispatched} preparation tasks for BatchJob #{job.id} successfully!")


@cli.command()
@click.option("--job-id", required=False, type=int, help="Batch Job ID to inspect")
def batch_status(job_id):
    """Check status and performance metrics for Batch OCR jobs."""
    from kalanjiyam.models.batch import BatchJob, BatchItem
    
    with Session(engine) as session:
        if job_id:
            job = session.query(BatchJob).get(job_id)
            if not job:
                raise click.ClickException(f"BatchJob ID {job_id} not found.")
            jobs = [job]
        else:
            jobs = session.query(BatchJob).order_by(BatchJob.id.desc()).limit(5).all()

        if not jobs:
            click.echo("No batch jobs found in database.")
            return

        for j in jobs:
            items = j.items
            total = len(items)
            completed = sum(1 for i in items if i.status == 'COMPLETED')
            failed = sum(1 for i in items if i.status == 'FAILED')
            in_progress = sum(1 for i in items if i.status in ('IN_PROGRESS', 'DOWNLOADED', 'IMAGES_EXTRACTED', 'OCR_IN_PROGRESS'))
            pending = sum(1 for i in items if i.status == 'PENDING')
            
            total_chunks = sum(len(i.chunks) for i in items)
            completed_chunks = sum(sum(1 for c in i.chunks if c.status == 'COMPLETED') for i in items)
            total_pages = sum(sum(len(c.pages) for c in i.chunks) for i in items)
            completed_pages = sum(sum(sum(1 for p in c.pages if p.status == 'COMPLETED') for c in i.chunks) for i in items)
            
            avg_extraction = [i.extraction_latency_ms for i in items if i.extraction_latency_ms is not None]
            avg_ocr = [i.total_ocr_latency_ms for i in items if i.total_ocr_latency_ms is not None]
            total_bytes = sum(i.source_size_bytes for i in items if i.source_size_bytes is not None)

            click.echo(f"=== BatchJob #{j.id} ===")
            click.echo(f"Target URI : {j.target_uri}")
            click.echo(f"Job Status : {j.status}")
            click.echo(f"Created At : {j.created_at}")
            if j.completed_at and j.created_at:
                dur_str = _format_duration((j.completed_at - j.created_at).total_seconds())
                click.echo(f"Time Taken : {dur_str}")
            elif j.created_at and j.status in ('PENDING', 'IN_PROGRESS'):
                dur_str = _format_duration((datetime.utcnow() - j.created_at).total_seconds())
                click.echo(f"Time Elapsed: {dur_str} (Running)")
                
            click.echo(f"Progress   : {completed}/{total} Items Completed ({failed} Failed, {in_progress} Processing, {pending} Pending)")
            if total_chunks > 0:
                click.echo(f"Chunks     : {completed_chunks}/{total_chunks} Completed")
            if total_pages > 0:
                click.echo(f"Pages      : {completed_pages}/{total_pages} Completed")
            
            item_durations = [(i.completed_at - i.created_at).total_seconds() for i in items if i.completed_at and i.created_at]
            if item_durations:
                avg_item_dur = _format_duration(sum(item_durations) / len(item_durations))
                click.echo(f"Avg Item Processing Time : {avg_item_dur}")

            if total_bytes:
                click.echo(f"Total Size : {total_bytes / (1024*1024):.2f} MB")
            if avg_extraction:
                click.echo(f"Avg Extraction Latency   : {sum(avg_extraction)/len(avg_extraction):.2f} ms")
            if avg_ocr:
                click.echo(f"Avg OCR Latency          : {sum(avg_ocr)/len(avg_ocr):.2f} ms")

            # Metadata extraction metrics (if enabled or present)
            project_ids = [i.project_id for i in items if i.project_id]
            meta_runs = []
            if project_ids:
                meta_runs = (
                    session.query(db.MetadataExtractionRun)
                    .filter(db.MetadataExtractionRun.project_id.in_(project_ids))
                    .order_by(db.MetadataExtractionRun.id.desc())
                    .all()
                )
            latest_meta_by_proj = {}
            for r in meta_runs:
                if r.project_id not in latest_meta_by_proj:
                    latest_meta_by_proj[r.project_id] = r

            if getattr(j, 'extract_metadata', False) or latest_meta_by_proj:
                meta_completed = sum(1 for r in latest_meta_by_proj.values() if r.status in ('COMPLETED', 'ok'))
                meta_in_prog = sum(1 for r in latest_meta_by_proj.values() if r.status in ('IN_PROGRESS', 'running'))
                meta_failed = sum(1 for r in latest_meta_by_proj.values() if r.status in ('FAILED', 'error'))
                meta_total = len(project_ids)
                click.echo(f"Metadata   : {meta_completed}/{meta_total} Described ({meta_in_prog} In Progress, {meta_failed} Failed) [enabled={getattr(j, 'extract_metadata', False)}]")
                
                total_fields = sum(r.fields_filled or 0 for r in latest_meta_by_proj.values())
                total_tokens = sum((r.total_prompt_tokens or 0) + (r.total_completion_tokens or 0) for r in latest_meta_by_proj.values())
                avg_cited = [r.evidence_verified_rate for r in latest_meta_by_proj.values() if r.evidence_verified_rate is not None]
                
                if latest_meta_by_proj:
                    click.echo(f"Meta Fields: {total_fields} total tags extracted across {len(latest_meta_by_proj)} project(s)")
                    if avg_cited:
                        click.echo(f"Meta Evidence: {sum(avg_cited)/len(avg_cited):.1%} average verified citations")
                    if total_tokens > 0:
                        click.echo(f"Meta Tokens: {total_tokens} tokens consumed")

            if failed > 0:
                click.echo("Failed Items:")
                for i in items:
                    if i.status == 'FAILED':
                        click.echo(f" - {i.file_path}: {i.error_message}")
            click.echo("")


@cli.command()
@click.option("--job-id", required=False, type=int, help="Batch Job ID (optional)")
def batch_promote_ocr(job_id):
    """Promote OCR output from ocr:tesseract track to role:p1 default active track."""
    from kalanjiyam.models.batch import BatchJob, BatchItem
    from kalanjiyam.utils.revisions import add_revision
    from kalanjiyam.enums import SitePageStatus
    
    with Session(engine) as session:
        if job_id:
            job = session.query(BatchJob).get(job_id)
            jobs = [job] if job else []
        else:
            jobs = session.query(BatchJob).all()

        promoted_count = 0
        bot_user = q.user("kalanjiyam-bot")
        
        for j in jobs:
            if not j:
                continue
            for item in j.items:
                if not item.project:
                    continue
                for page in item.project.pages:
                    pv_ocr = session.query(db.PageVersion).filter_by(page_id=page.id).filter(db.PageVersion.version_key.like("ocr:%")).first()
                    if not pv_ocr:
                        continue
                    rev_ocr = session.query(db.Revision).filter_by(page_version_id=pv_ocr.id).order_by(db.Revision.id.desc()).first()
                    if not rev_ocr or not rev_ocr.content:
                        continue
                        
                    pv_p1 = session.query(db.PageVersion).filter_by(page_id=page.id, version_key="role:p1").first()
                    p1_ver = pv_p1.version if pv_p1 else 0
                    
                    from kalanjiyam.utils.document_storage import load_revision_document

                    add_revision(
                        page=page,
                        summary="Promoted Batch OCR to Default Track",
                        content=rev_ocr.content,
                        status=SitePageStatus.R0,
                        version=p1_ver,
                        author_id=bot_user.id if bot_user else None,
                        document=load_revision_document(rev_ocr),
                        content_format=rev_ocr.content_format or "plain",
                        version_key="role:p1",
                    )
                    promoted_count += 1
                    
        click.echo(f"Successfully promoted {promoted_count} pages to the default active editor track (role:p1)!")


@cli.command("migrate-to-s3")
@click.option("--batch-size", default=100, help="Commit every N records.")
@click.option(
    "--clear-db",
    is_flag=True,
    default=False,
    help="Nullify DB columns after successful S3 upload (saves DB space).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be migrated without writing anything.",
)
def migrate_to_s3(batch_size, clear_db, dry_run):
    """Migrate OCR payloads and revision documents from PostgreSQL to S3/VersityGW.

    This command is idempotent: records that already exist in object storage
    are skipped automatically.
    """
    import gzip
    import json

    from kalanjiyam.utils.storage import (
        get_storage,
        page_ocr_key,
        revision_document_key,
    )

    import os

    env_name = os.environ.get("KALANJIYAM_ENVIRONMENT", "development")
    app = kalanjiyam.create_app(env_name)
    with app.app_context():
        storage = get_storage()
        session = q.get_session()

        from kalanjiyam.utils.document_storage import (
            derive_revision_tag,
            get_page_revision_index,
            save_page_ocr,
            save_revision_document,
        )

        # --- Phase 1: Page OCR Bounding Boxes ---
        pages = (
            session.query(db.Page)
            .filter(db.Page.ocr_bounding_boxes.isnot(None))
            .all()
        )
        click.echo(f"[Phase 1] Found {len(pages)} pages with OCR data in Postgres.")

        migrated_ocr = 0
        skipped_ocr = 0
        failed_ocr = 0

        for page in pages:
            if not page.ocr_bounding_boxes:
                continue

            project = page.project
            if project is None:
                continue

            key = page_ocr_key(project.slug, page.slug)

            try:
                if storage.exists(key):
                    skipped_ocr += 1
                    continue
            except Exception as err:
                click.echo(f"  [WARNING] Could not check existence of {key}: {err}")

            if dry_run:
                click.echo(f"  [DRY RUN] Would upload OCR for page {page.slug}")
                migrated_ocr += 1
                continue

            try:
                success = save_page_ocr(page, page.ocr_bounding_boxes)
                if success:
                    migrated_ocr += 1
                    if clear_db:
                        page.ocr_bounding_boxes = None
                else:
                    failed_ocr += 1
                    click.echo(f"  [WARNING] OCR save to storage returned false for page {page.slug}")
            except Exception as err:
                failed_ocr += 1
                click.echo(f"  [ERROR] Failed to migrate OCR for page {page.slug}: {err}")

            if (migrated_ocr + skipped_ocr + failed_ocr) % batch_size == 0:
                if clear_db and migrated_ocr > 0:
                    try:
                        session.commit()
                    except Exception as err:
                        session.rollback()
                        click.echo(f"  [ERROR] DB commit failed during batch: {err}")
                click.echo(
                    f"  Progress: {migrated_ocr + skipped_ocr + failed_ocr}/{len(pages)} pages processed."
                )

        if clear_db and not dry_run and migrated_ocr > 0:
            try:
                session.commit()
            except Exception as err:
                session.rollback()
                click.echo(f"  [ERROR] Final DB commit failed for Phase 1: {err}")

        click.echo(
            f"[✓] Phase 1 complete. Migrated: {migrated_ocr} | Skipped: {skipped_ocr} | Failed: {failed_ocr}"
        )

        # --- Phase 2: Revision Document JSON ---
        revisions = (
            session.query(db.Revision)
            .filter(db.Revision.document.isnot(None))
            .all()
        )
        click.echo(
            f"[Phase 2] Found {len(revisions)} revisions with document JSON in Postgres."
        )

        migrated_rev = 0
        skipped_rev = 0
        failed_rev = 0

        for rev in revisions:
            if not rev.document:
                continue

            page = rev.page
            project = rev.project
            if page is None or project is None:
                continue

            v_num = get_page_revision_index(rev)
            tag = derive_revision_tag(rev)
            key = revision_document_key(project.slug, page.slug, v_num, tag=tag)

            try:
                if storage.exists(key):
                    skipped_rev += 1
                    continue
            except Exception as err:
                click.echo(f"  [WARNING] Could not check existence of {key}: {err}")

            if dry_run:
                click.echo(
                    f"  [DRY RUN] Would upload document for revision {rev.id} (key: {key})"
                )
                migrated_rev += 1
                continue

            try:
                success = save_revision_document(rev, rev.document)
                if success:
                    migrated_rev += 1
                    if clear_db:
                        rev.document = None
                else:
                    failed_rev += 1
                    click.echo(f"  [WARNING] Document save to storage returned false for revision {rev.id}")
            except Exception as err:
                failed_rev += 1
                click.echo(f"  [ERROR] Failed to migrate revision {rev.id}: {err}")

            if (migrated_rev + skipped_rev + failed_rev) % batch_size == 0:
                if clear_db and migrated_rev > 0:
                    try:
                        session.commit()
                    except Exception as err:
                        session.rollback()
                        click.echo(f"  [ERROR] DB commit failed during batch: {err}")
                click.echo(
                    f"  Progress: {migrated_rev + skipped_rev + failed_rev}/{len(revisions)} revisions processed."
                )

        if clear_db and not dry_run and migrated_rev > 0:
            try:
                session.commit()
            except Exception as err:
                session.rollback()
                click.echo(f"  [ERROR] Final DB commit failed for Phase 2: {err}")

        click.echo(
            f"[✓] Phase 2 complete. Migrated: {migrated_rev} | Skipped: {skipped_rev} | Failed: {failed_rev}"
        )

        click.echo()
        click.echo("=" * 60)
        click.echo("MIGRATION FINAL SUMMARY:")
        click.echo(f"  OCR Pages   — Migrated: {migrated_ocr} | Skipped: {skipped_ocr} | Failed: {failed_ocr}")
        click.echo(f"  Revisions   — Migrated: {migrated_rev} | Skipped: {skipped_rev} | Failed: {failed_rev}")
        click.echo("=" * 60)

        if clear_db and not dry_run and (failed_ocr == 0 and failed_rev == 0):
            click.echo(
                "\n[✓] All records migrated cleanly and DB columns nullified.\n"
                "Run 'VACUUM FULL proof_pages;' and 'VACUUM FULL proof_revisions;' in PostgreSQL to reclaim disk space."
            )
        elif clear_db and (failed_ocr > 0 or failed_rev > 0):
            click.echo(
                "\n[!] Note: Some records failed to upload. Their data remains safely stored in PostgreSQL DB.\n"
                "Run 'python cli.py reconcile-storage' later to retry the failed records."
            )
        elif not clear_db:
            click.echo(
                "\n[!] Note: DB columns were NOT cleared (use --clear-db to nullify DB columns after verifying)."
            )


@cli.command("reconcile-storage")
@click.option("--limit", default=500, help="Maximum number of items to reconcile per run.")
def reconcile_storage(limit):
    """Health check S3/VersityGW and push any temporary DB fallback data to S3."""
    from kalanjiyam.utils.document_storage import is_storage_healthy, reconcile_db_to_storage

    import os

    env_name = os.environ.get("KALANJIYAM_ENVIRONMENT", "development")
    app = kalanjiyam.create_app(env_name)
    with app.app_context():
        click.echo("[*] Checking S3 / VersityGW storage health...")
        if not is_storage_healthy():
            click.echo("[✗] Storage is currently unreachable or offline. Try again later.")
            return

        click.echo("[✓] Storage is online. Reconciling DB fallback records to S3...")
        stats = reconcile_db_to_storage(limit=limit)
        click.echo(
            f"[✓] Reconciled {stats['reconciled_ocr']} page OCR records and "
            f"{stats['reconciled_revisions']} revision documents to S3."
        )


@cli.command("storage-stats")
def storage_stats():
    """Compare storage size of gzipped JSON S3 payloads vs uncompressed DB payloads."""
    from scripts.compare_storage_savings import main as run_stats

    run_stats()


# Search index
# ============


def _search_app_context(app_env):
    """OpenSearch settings come from Flask config, so search commands need one."""
    from config import create_config_only_app

    return create_config_only_app(app_env).app_context()


def _resolve_org_id(session, org_slug):
    """Turn an --org slug into a group id, or fail loudly."""
    if not org_slug:
        return None
    org = session.query(db.Group).filter_by(slug=org_slug).first()
    if org is None:
        raise click.ClickException(f'Organization "{org_slug}" not found.')
    return org.id


def _resolve_project_id(session, project_slug):
    if not project_slug:
        return None
    project = session.query(db.Project).filter_by(slug=project_slug).first()
    if project is None:
        raise click.ClickException(f'Project "{project_slug}" not found.')
    return project.id


def _create_search_job(session, *, job_type, org_id=None, project_id=None):
    from kalanjiyam.models.search import (
        SCOPE_ALL,
        SCOPE_ORG,
        SCOPE_PROJECT,
        SearchIndexJob,
    )

    if project_id:
        scope_kind = SCOPE_PROJECT
    elif org_id:
        scope_kind = SCOPE_ORG
    else:
        scope_kind = SCOPE_ALL

    job = SearchIndexJob(
        job_type=job_type,
        scope_kind=scope_kind,
        scope_org_id=org_id,
        scope_project_id=project_id,
    )
    session.add(job)
    # Commit before dispatching: the worker looks the job up by id.
    session.commit()
    return job


@cli.group("search-index")
def search_index():
    """Manage the OpenSearch full-text index."""


@search_index.command("init")
@click.option("--env", "app_env", default="development", help="Application environment")
def search_index_init(app_env):
    """Create empty indices and aliases for every organization."""
    from kalanjiyam.search import indexer
    from kalanjiyam.search.client import get_client, get_settings, is_enabled

    with _search_app_context(app_env):
        if not is_enabled():
            raise click.ClickException("SEARCH_ENABLED is false; nothing to do.")
        settings = get_settings()
        client = get_client()
        with Session(engine) as session:
            group_ids = indexer.all_group_ids(session)
            for group_id in group_ids:
                indexer.ensure_org_indices(client, settings.index_prefix, group_id)
                click.echo(f"Ready: organization {group_id}")
            orphans = indexer.ungrouped_project_count(session)
        click.echo(f"Initialized indices for {len(group_ids)} organization(s).")
        if orphans:
            click.echo(
                f"Warning: {orphans} project(s) belong to no organization and "
                "will not be indexed. Attach them to a group first."
            )


@search_index.command("rebuild")
@click.option("--org", "org_slug", help="Rebuild one organization only")
@click.option("--project", "project_slug", help="Reindex one project only")
@click.option("--env", "app_env", default="development", help="Application environment")
@click.option("--now", "run_inline", is_flag=True, help="Run in this process instead of queueing")
def search_index_rebuild(org_slug, project_slug, app_env, run_inline):
    """Rebuild the index, swapping aliases only once the build succeeds."""
    from kalanjiyam.models.search import JOB_REBUILD
    from kalanjiyam.tasks.search_index import rebuild_index

    with _search_app_context(app_env):
        with Session(engine) as session:
            org_id = _resolve_org_id(session, org_slug)
            project_id = _resolve_project_id(session, project_slug)
            job = _create_search_job(
                session, job_type=JOB_REBUILD, org_id=org_id, project_id=project_id
            )
            job_id = job.id

        if run_inline:
            click.echo(f"Running rebuild job {job_id} in this process...")
            rebuild_index(job_id)
            click.echo("Done. Check `./cli.py search-index status` for the result.")
        else:
            rebuild_index.apply_async(args=[job_id], queue="search_index")
            click.echo(f"Queued rebuild as job {job_id} on the search_index queue.")


@search_index.command("sync")
@click.option("--org", "org_slug", help="Sync one organization only")
@click.option("--env", "app_env", default="development", help="Application environment")
@click.option("--now", "run_inline", is_flag=True, help="Run in this process instead of queueing")
def search_index_sync(org_slug, app_env, run_inline):
    """Reconcile the index with the database without a full rebuild."""
    from kalanjiyam.models.search import JOB_SYNC
    from kalanjiyam.tasks.search_index import sync_index

    with _search_app_context(app_env):
        with Session(engine) as session:
            org_id = _resolve_org_id(session, org_slug)
            job = _create_search_job(session, job_type=JOB_SYNC, org_id=org_id)
            job_id = job.id

        if run_inline:
            click.echo(f"Running sync job {job_id} in this process...")
            sync_index(job_id)
            click.echo("Done. Check `./cli.py search-index status` for the result.")
        else:
            sync_index.apply_async(args=[job_id], queue="search_index")
            click.echo(f"Queued sync as job {job_id} on the search_index queue.")


@search_index.command("drop")
@click.option("--org", "org_slug", required=True, help="Organization whose index to delete")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
@click.option("--env", "app_env", default="development", help="Application environment")
def search_index_drop(org_slug, yes, app_env):
    """Delete an organization's indices. The data can be rebuilt from the database."""
    from kalanjiyam.search import indexer
    from kalanjiyam.search.client import get_client, get_settings, is_enabled

    with _search_app_context(app_env):
        if not is_enabled():
            raise click.ClickException("SEARCH_ENABLED is false; nothing to do.")
        with Session(engine) as session:
            org_id = _resolve_org_id(session, org_slug)
        if not yes:
            click.confirm(
                f'Delete every search index for organization "{org_slug}"?', abort=True
            )
        settings = get_settings()
        removed = indexer.drop_org_indices(get_client(), settings.index_prefix, org_id)
        click.echo(f"Deleted {len(removed)} index/indices: {', '.join(removed) or '(none)'}")


@search_index.command("status")
@click.option("--job-id", type=int, help="Show one job in detail")
@click.option("--limit", default=10, type=int, help="Number of recent jobs to list")
@click.option("--env", "app_env", default="development", help="Application environment")
def search_index_status(job_id, limit, app_env):
    """Show cluster health, per-organization document counts, and recent jobs."""
    from kalanjiyam.models.search import SearchIndexJob
    from kalanjiyam.search import indexer
    from kalanjiyam.search.client import get_client, get_settings, health

    with _search_app_context(app_env):
        info = health()
        click.echo(f"Search enabled : {info['enabled']}")
        click.echo(f"Cluster status : {info['status']}")
        if info.get("error"):
            click.echo(f"Cluster error  : {info['error']}")

        with Session(engine) as session:
            if info["enabled"] and info["reachable"]:
                settings = get_settings()
                client = get_client()
                click.echo("")
                click.echo(f"{'Org':<8} | {'Pages':<10} | {'Projects':<10} | Size")
                click.echo("-" * 50)
                for group_id in indexer.all_group_ids(session):
                    s = indexer.org_stats(client, settings.index_prefix, group_id)
                    size_mb = (
                        s["pages_size_bytes"] + s["projects_size_bytes"]
                    ) / (1024 * 1024)
                    click.echo(
                        f"{group_id:<8} | {s['pages_count']:<10} | "
                        f"{s['projects_count']:<10} | {size_mb:.1f} MB"
                    )
                orphans = indexer.ungrouped_project_count(session)
                if orphans:
                    click.echo("")
                    click.echo(
                        f"Unindexed (no organization): {orphans} project(s)"
                    )

            click.echo("")
            query = session.query(SearchIndexJob)
            if job_id:
                query = query.filter(SearchIndexJob.id == job_id)
            jobs = query.order_by(SearchIndexJob.id.desc()).limit(limit).all()
            if not jobs:
                click.echo("No index jobs recorded yet.")
                return

            click.echo(
                f"{'ID':<5} | {'Type':<14} | {'Scope':<9} | {'Status':<12} | "
                f"{'Docs':<12} | Duration"
            )
            click.echo("-" * 80)
            for job in jobs:
                if job.started_at and job.completed_at:
                    duration = _format_duration(
                        (job.completed_at - job.started_at).total_seconds()
                    )
                else:
                    duration = "N/A"
                docs = f"{job.processed_docs}/{job.total_docs}"
                click.echo(
                    f"{job.id:<5} | {job.job_type:<14} | {job.scope_kind:<9} | "
                    f"{job.status:<12} | {docs:<12} | {duration}"
                )
                if job.error_message:
                    click.echo(f"      error: {job.error_message}")


@cli.command()
@click.option("--project", "slugs", multiple=True, help="project slug; repeatable")
@click.option("--all", "all_projects", is_flag=True, help="every project with pages")
@click.option(
    "--force", is_flag=True, help="re-read every window, ignoring the text-hash cache"
)
@click.option(
    "--local",
    is_flag=True,
    help="run in this process instead of enqueueing to the metadata queue",
)
@click.option("--limit", type=int, default=0, help="stop after N projects (0 = no limit)")
def metadata_extract(slugs, all_projects, force, local, limit):
    """Run archival description extraction over one or more projects.

    Enqueues to the `metadata` Celery queue by default, so a worker must be
    consuming it (`-Q metadata`). `--local` runs the task inline instead, which
    is what you want for one document you are watching, and which is the only
    mode that reports per-document results here.

    Extraction is incremental: windows whose text has not changed since the last
    completed run are reused, so re-running after a proofreading fix costs a call
    or two rather than a whole document. `--force` disables that.
    """
    if not slugs and not all_projects:
        raise click.ClickException("Pass --project SLUG (repeatable) or --all.")

    # `Session(engine)` rather than `create_app`, like every other command here.
    # `create_app` runs the startup sanity checks, which abort on any drift
    # between the seeded lookup tables and the enums -- fine for serving a site,
    # wrong for a maintenance command whose job may be to fix that drift. The
    # task supplies its own config-only app context when it needs one.
    from kalanjiyam.tasks import archival_extract

    with Session(engine) as session:
        if all_projects:
            projects = session.query(db.Project).order_by(db.Project.id).all()
        else:
            projects = []
            for slug in slugs:
                project = session.query(db.Project).filter_by(slug=slug).first()
                if project is None:
                    raise click.ClickException(f"No project with slug {slug!r}.")
                projects.append(project)

        if limit:
            projects = projects[:limit]

        if not projects:
            click.echo("No projects matched.")
            return

        for project in projects:
            if local:
                click.echo(f"{project.slug}: extracting...")
                # `.apply()` rather than the private body: it keeps the Redis
                # lock, so a CLI run and a UI run cannot collide.
                result = archival_extract.extract_archival_metadata.apply(
                    args=[project.id], kwargs={"force": force}
                ).get()
                run = None
                if result.get("run_id"):
                    run = session.query(db.MetadataExtractionRun).get(result["run_id"])
                if run is not None:
                    click.echo(
                        f"{project.slug}: {run.status} "
                        f"pages {run.pages_read or 0}/{run.pages_total or 0} "
                        f"windows {run.windows_completed or 0}/{run.windows_total or 0} "
                        f"tags {run.fields_filled or 0}/{run.fields_total or 0} "
                        f"tokens {run.total_tokens if run.total_tokens is not None else '?'}"
                    )
                else:
                    click.echo(f"{project.slug}: {result.get('status', '?')}")
                if result.get("error"):
                    click.echo(f"      error: {result['error']}")
            else:
                archival_extract.extract_archival_metadata.delay(project.id, force=force)
                click.echo(f"{project.slug}: queued")

        if not local:
            click.echo(
                f"\nQueued {len(projects)} project(s) on the `metadata` queue. "
                "A worker must be running with -Q metadata, or they will sit there."
            )


@cli.command()
@click.option("--project", "slug", help="only this project")
@click.option("--limit", type=int, default=20, help="how many runs to show")
def metadata_runs(slug, limit):
    """List recent archival extraction runs.

    The same figures the Extraction Metrics dashboard shows, for when you are
    driving a batch from a terminal. Blank confidence and token columns mean the
    service reported none, which is not the same as zero.
    """
    with Session(engine) as session:
        query = session.query(db.MetadataExtractionRun)

        if slug:
            project = session.query(db.Project).filter_by(slug=slug).first()
            if project is None:
                raise click.ClickException(f"No project with slug {slug!r}.")
            query = query.filter(db.MetadataExtractionRun.project_id == project.id)

        runs = query.order_by(db.MetadataExtractionRun.id.desc()).limit(limit).all()
        if not runs:
            click.echo("No extraction runs recorded yet.")
            return

        projects = {
            p.id: p.slug
            for p in session.query(db.Project).filter(
                db.Project.id.in_({r.project_id for r in runs})
            )
        }

        click.echo(
            f"{'ID':<5} | {'Project':<28} | {'Status':<10} | {'Pages':<11} | "
            f"{'Tags':<7} | {'Cited':<6} | {'Time':<8} | Tokens"
        )
        click.echo("-" * 105)
        for run in runs:
            pages = f"{run.pages_read or 0}/{run.pages_total or 0}"
            tags = f"{run.fields_filled or 0}/{run.fields_total or 0}"
            cited = (
                f"{run.evidence_verified_rate * 100:.0f}%"
                if run.evidence_verified_rate is not None
                else "-"
            )
            tokens = run.total_tokens if run.total_tokens is not None else "-"
            time_str = run.time_taken_display if run.duration_sec is not None else "-"
            click.echo(
                f"{run.id:<5} | {projects.get(run.project_id, '(deleted)'):<28} | "
                f"{run.status:<10} | {pages:<11} | {tags:<7} | {cited:<6} | {time_str:<8} | {tokens}"
            )
            if run.error_message:
                click.echo(f"      error: {run.error_message}")


if __name__ == "__main__":
    cli()
