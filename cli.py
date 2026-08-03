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
def batch_ocr(s3_uri, local_uri, org, pdf, image, lang):
    """Start a Batch OCR process for PDFs and Image folders from S3 or Local."""
    import boto3
    import os
    from urllib.parse import urlparse
    from kalanjiyam.models.batch import BatchJob, BatchItem
    from kalanjiyam.tasks.s3_batch import process_s3_batch_item
    import mimetypes
    
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
                    continue
                    
                if mime_type == 'application/pdf' and process_pdfs:
                    items_to_process.append({
                        'path': f"s3://{bucket_name}/{key}",
                        'mime_type': mime_type,
                        'type': 'pdf'
                    })
                elif mime_type.startswith('image/') and process_images:
                    parent_path = key.rsplit('/', 1)[0] if '/' in key else ''
                    if parent_path not in image_groups:
                        image_groups[parent_path] = []
                    image_groups[parent_path].append(key)
                    
        if process_images:
            for parent_path, image_keys in image_groups.items():
                items_to_process.append({
                    'path': f"s3://{bucket_name}/{parent_path}/",
                    'mime_type': 'image_folder',
                    'type': 'image_folder',
                    'count': len(image_keys)
                })
                
    elif local_uri:
        click.echo(f"Scanning Local Directory {local_uri} recursively...")
        
        if not os.path.isdir(local_uri):
             raise click.ClickException(f"Local path does not exist or is not a directory: {local_uri}")
             
        for root, dirs, files in os.walk(local_uri):
            for file in files:
                file_path = os.path.join(root, file)
                # Convert Windows paths to forward slashes for consistency if needed, but not strictly necessary for file://
                file_path_clean = file_path.replace('\\', '/')
                mime_type, _ = mimetypes.guess_type(file_path_clean)
                
                if not mime_type:
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
        job = BatchJob(target_uri=target_uri, status='PENDING')
        session.add(job)
        session.flush()
        
        click.echo(f"Created BatchJob ID: {job.id}. Dispatching items...")
        
        db_items = []
        for item_data in items_to_process:
            item = BatchItem(
                job_id=job.id,
                file_path=item_data['path'],
                mime_type=item_data['mime_type'],
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
                queue='s3_batch'
            )
            
        click.echo(f"Dispatched {len(db_items)} items to the s3_batch Celery queue successfully!")


@cli.command("import-jsonl")
@click.option("--jsonl-uri", required=True, help="S3 JSONL prefix (s3://bucket/prefix/)")
@click.option("--pdf-uri", required=True, help="S3 PDF prefix (s3://bucket/prefix/)")
@click.option("--org", required=True, help="Organization slug for imported projects")
@click.option("--dry-run", is_flag=True, help="Discover and validate without writing DB or storage")
@click.option(
    "--allow-duplicate",
    default=False,
    type=bool,
    help="Allow importing books that already exist in the organization (default: false)",
)
def import_jsonl(jsonl_uri, pdf_uri, org, dry_run, allow_duplicate):
    """Import PDF pages and JSONL OCR records from S3 (JSONL page numbers are 1-based)."""
    from kalanjiyam.models.group import Group
    from kalanjiyam.services.jsonl_import import ImportValidationError, run_import

    org = slugify(org)
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
    click.echo(f"Duplicate pages: {summary.duplicate_pages}")
    click.echo(f"Malformed records: {summary.malformed_records}")
    click.echo(f"Skipped books (duplicates): {summary.skipped_books}")
    click.echo(f"Importable books: {summary.importable_books}")
    click.echo(f"Invalid books: {summary.invalid_books}")
    if dry_run:
        click.echo("Dry run: no database or storage writes performed.")


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
def batch_retry(job_id, org, lang):
    """Retry failed or stuck items/chunks in a Batch OCR job without re-scanning."""
    from kalanjiyam.models.batch import BatchJob, BatchItem, BatchOcrChunk, BatchOcrPage
    from kalanjiyam.tasks.s3_batch import process_s3_batch_item, process_s3_batch_chunk
    
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
            
        items_to_retry = [
            item for item in job.items
            if item.status in ('FAILED', 'PENDING', 'IN_PROGRESS', 'IMAGES_EXTRACTED')
            or any(page.status == 'FAILED' for page in item.ocr_pages)
        ]
        
        if not items_to_retry:
            click.echo(f"Job #{job_id} has no items to retry.")
            return
            
        job.status = 'IN_PROGRESS'
        job.error_message = None
        job.completed_at = None

        chunks_dispatched = 0
        items_dispatched = 0
        
        for item in items_to_retry:
            if item.project_id and item.chunks:
                item.status = 'IN_PROGRESS'
                item.error_message = None
                item.completed_at = None
                
                for chunk in item.chunks:
                    has_failed_pages = any(page.status == 'FAILED' for page in chunk.pages)
                    if chunk.status in ('FAILED', 'PENDING', 'IN_PROGRESS') or has_failed_pages:
                        chunk.status = 'PENDING'
                        chunk.error_message = None
                        chunk.completed_at = None
                        
                        for ocr_p in chunk.pages:
                            if ocr_p.status != 'COMPLETED':
                                ocr_p.status = 'PENDING'
                                ocr_p.error_message = None
                                ocr_p.completed_at = None
                                
                        session.commit()
                        process_s3_batch_chunk.apply_async(
                            args=[chunk.id, org, lang],
                            queue='s3_batch'
                        )
                        chunks_dispatched += 1
            else:
                item.status = 'PENDING'
                item.error_message = None
                session.commit()
                process_s3_batch_item.apply_async(
                    args=[item.id, org, lang],
                    queue='s3_batch'
                )
                items_dispatched += 1
            
        session.commit()
        click.echo(f"Re-dispatched {chunks_dispatched} chunk tasks and {items_dispatched} preparation tasks for BatchJob #{job.id} successfully!")


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
                    
                    add_revision(
                        page=page,
                        summary="Promoted Batch OCR to Default Track",
                        content=rev_ocr.content,
                        status=SitePageStatus.R0,
                        version=p1_ver,
                        author_id=bot_user.id if bot_user else None,
                        document=rev_ocr.document,
                        content_format=rev_ocr.content_format or "plain",
                        version_key="role:p1",
                    )
                    promoted_count += 1
                    
        click.echo(f"Successfully promoted {promoted_count} pages to the default active editor track (role:p1)!")


if __name__ == "__main__":
    cli()
