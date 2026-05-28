#!/usr/bin/env python3

import getpass
from pathlib import Path

import click
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

        slug = slugify(title)
        page_image_dir = (
            Path(current_app.config["UPLOAD_FOLDER"]) / "projects" / slug / "pages"
        )
        page_image_dir.mkdir(parents=True, exist_ok=True)
        create_project_inner(
            display_title=title,
            pdf_path=pdf_path,
            output_dir=str(page_image_dir),
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
    """Create a super-admin user (CLI-only flow)."""
    username = input("Username: ")
    email = input("Email: ")
    raw_password = getpass.getpass("Password: ")

    with Session(engine) as session:
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


if __name__ == "__main__":
    cli()
