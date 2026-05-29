"""Common queries.

We use this module to organize repetitive query logic and keep our views readable.
For simple or adhoc queries, you can just write them in their corresponding view.
"""

import functools

from flask import current_app
from sqlalchemy import create_engine
from sqlalchemy.orm import load_only, scoped_session, selectinload, sessionmaker

import kalanjiyam.database as db

# NOTE: this logic is copied from Flask-SQLAlchemy. We avoid Flask-SQLAlchemy
# because we also need to access the database from a non-Flask context when
# we run database seed scripts.
# ~~~
# Scope the session to the current greenlet if greenlet is available,
# otherwise fall back to the current thread.
try:
    from greenlet import getcurrent as _ident_func
except ImportError:
    from threading import get_ident as _ident_func


# functools.cache makes this return value a singleton.
@functools.cache
def get_engine():
    database_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    return create_engine(database_uri)


# functools.cache makes this return value a singleton.
@functools.cache
def get_session_class():
    # Scoped sessions remove various kinds of errors, e.g. when using database
    # objects created on different threads.
    #
    # For details, see:
    # - https://stackoverflow.com/questions/12223335
    # - https://flask.palletsprojects.com/en/2.1.x/patterns/sqlalchemy/
    session_factory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return scoped_session(session_factory, scopefunc=_ident_func)


def get_session():
    """Instantiate a scoped session.

    If we implemented this right, there should be exactly one unique session
    per request.
    """
    Session = get_session_class()
    return Session()


def texts() -> list[db.Text]:
    """Return a list of all texts in no particular older."""
    session = get_session()
    return session.query(db.Text).all()


def page_statuses() -> list[db.PageStatus]:
    session = get_session()
    return session.query(db.PageStatus).all()


def text(slug: str) -> db.Text | None:
    session = get_session()
    return (
        session.query(db.Text)
        .filter_by(slug=slug)
        .options(
            selectinload(db.Text.sections).load_only(
                db.TextSection.slug,
                db.TextSection.title,
            )
        )
        .first()
    )


def text_meta(slug: str) -> db.Text:
    """Return only specific fields from the given text."""
    # TODO: is this method even useful? Is there a performance penalty for
    # using just `text`?
    session = get_session()
    return (
        session.query(db.Text)
        .filter_by(slug=slug)
        .options(
            load_only(
                db.Text.id,
                db.Text.slug,
            )
        )
        .first()
    )


def text_section(text_id: int, slug: str) -> db.TextSection | None:
    session = get_session()
    return session.query(db.TextSection).filter_by(text_id=text_id, slug=slug).first()


def block(text_id: int, slug: str) -> db.TextBlock | None:
    session = get_session()
    return session.query(db.TextBlock).filter_by(text_id=text_id, slug=slug).first()


def block_parse(block_id: int) -> db.BlockParse | None:
    session = get_session()
    return session.query(db.BlockParse).filter_by(block_id=block_id).first()


def dictionaries() -> list[db.Dictionary]:
    session = get_session()
    return session.query(db.Dictionary).all()


def dict_entries(
    sources: list[str], keys: list[str]
) -> dict[str, list[db.DictionaryEntry]]:
    """
    :param sources: slugs of the dictionaries to query
    :param keys: the keys (dictionary entries) to query
    """
    session = get_session()
    dicts = dictionaries()
    source_ids = [d.id for d in dicts if d.slug in sources]

    rows = (
        session.query(db.DictionaryEntry)
        .filter(
            (db.DictionaryEntry.dictionary_id.in_(source_ids))
            & (db.DictionaryEntry.key.in_(keys))
        )
        .all()
    )

    dict_id_to_slug = {d.id: d.slug for d in dicts}
    mapping = {s: [] for s in sources}
    for row in rows:
        dict_slug = dict_id_to_slug[row.dictionary_id]
        mapping[dict_slug].append(row)
    return mapping


def projects() -> list[db.Project]:
    """Return all projects in no particular order."""
    session = get_session()
    return session.query(db.Project).all()


def project(slug: str) -> db.Project | None:
    session = get_session()
    return session.query(db.Project).filter(db.Project.slug == slug).first()


def thread(*, id: int) -> db.Thread | None:
    session = get_session()
    return session.query(db.Thread).filter_by(id=id).first()


def post(*, id: int) -> db.Post | None:
    session = get_session()
    return session.query(db.Post).filter_by(id=id).first()


def create_thread(*, board_id: int, user_id: int, title: str, content: str):
    session = get_session()

    assert board_id
    thread = db.Thread(board_id=board_id, author_id=user_id, title=title)
    session.add(thread)
    session.flush()

    post = db.Post(
        board_id=board_id, author_id=user_id, thread_id=thread.id, content=content
    )
    session.add(post)
    session.commit()


def create_post(*, board_id: int, thread: db.Thread, user_id: int, content: str):
    session = get_session()
    post = db.Post(
        board_id=board_id, author_id=user_id, thread_id=thread.id, content=content
    )
    session.add(post)
    session.flush()

    assert post.created_at
    thread.updated_at = post.created_at
    session.add(thread)
    session.commit()


def page(project_id, page_slug: str) -> db.Page | None:
    session = get_session()
    return (
        session.query(db.Page)
        .filter((db.Page.project_id == project_id) & (db.Page.slug == page_slug))
        .first()
    )


def user(username: str) -> db.User | None:
    session = get_session()
    return (
        session.query(db.User)
        .filter_by(username=username, is_deleted=False, is_banned=False)
        .first()
    )


def create_user(*, username: str, email: str, raw_password: str) -> db.User:
    session = get_session()
    user = db.User(username=username, email=email)
    user.set_password(raw_password)
    session.add(user)
    session.flush()

    # Allow all users to be proofreaders
    proofreader_role = (
        session.query(db.Role).filter_by(name=db.SiteRole.P1.value).first()
    )
    user_role = db.UserRoles(user_id=user.id, role_id=proofreader_role.id)
    session.add(user_role)

    session.commit()
    return user


def organization_by_slug(slug: str) -> db.Group | None:
    session = get_session()
    return session.query(db.Group).filter_by(slug=slug).first()


def create_organization(*, name: str, slug: str, description: str = "") -> db.Group:
    session = get_session()
    group = db.Group(name=name, slug=slug, description=description)
    session.add(group)
    session.commit()
    return group


def set_user_organization(*, user: db.User, organization: db.Group | None) -> None:
    session = get_session()
    user.organization_id = organization.id if organization else None
    session.add(user)

    # Keep user_groups synchronized for backward-compatible joins.
    session.query(db.UserGroups).filter_by(user_id=user.id).delete()
    if organization is not None:
        session.add(db.UserGroups(user_id=user.id, group_id=organization.id))
    session.commit()


def blog_post(slug: str) -> db.BlogPost | None:
    """Fetch the given blog post."""
    session = get_session()
    return session.query(db.BlogPost).filter_by(slug=slug).first()


def blog_posts() -> list[db.BlogPost]:
    """Fetch all blog posts."""
    session = get_session()
    return session.query(db.BlogPost).order_by(db.BlogPost.created_at.desc()).all()


def project_sponsorships() -> list[db.ProjectSponsorship]:
    session = get_session()
    results = session.query(db.ProjectSponsorship).all()
    return sorted(results, key=lambda s: s.sa_title or s.en_title)


def contributor_info() -> list[db.ContributorInfo]:
    session = get_session()
    return session.query(db.ContributorInfo).order_by(db.ContributorInfo.name).all()


def genres() -> list[db.Genre]:
    session = get_session()
    return session.query(db.Genre).all()


# --- Group queries (for super-admin group management) ---


def groups() -> list[db.Group]:
    """Return all groups in no particular order."""
    session = get_session()
    return session.query(db.Group).all()


def groups_paginated(page: int = 1, per_page: int = 20) -> tuple[list[db.Group], int]:
    """Return a page of groups and total count. 1-based page."""
    session = get_session()
    q = session.query(db.Group)
    total = q.count()
    items = (
        q.order_by(db.Group.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def group(group_id: int) -> db.Group | None:
    """Return the group by id."""
    session = get_session()
    return session.query(db.Group).filter_by(id=group_id).first()


def users_in_group(group_id: int) -> list[db.User]:
    """Return users that belong to the given group."""
    session = get_session()
    return (
        session.query(db.User)
        .join(db.UserGroups, db.User.id == db.UserGroups.user_id)
        .filter(db.UserGroups.group_id == group_id)
        .filter(db.User.is_deleted.is_(False), db.User.is_banned.is_(False))
        .order_by(db.User.username)
        .all()
    )


def texts_in_group(
    group_id: int, page: int = 1, per_page: int = 20
) -> tuple[list[db.Text], int]:
    """Return a page of texts in the group and total count. 1-based page."""
    session = get_session()
    q = (
        session.query(db.Text)
        .join(db.TextGroups, db.Text.id == db.TextGroups.text_id)
        .filter(db.TextGroups.group_id == group_id)
    )
    total = q.count()
    items = (
        q.order_by(db.Text.slug)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def add_user_to_group(user_id: int, group_id: int) -> None:
    """Add a user to a group. Idempotent. Syncs users.organization_id."""
    session = get_session()
    existing = (
        session.query(db.UserGroups)
        .filter_by(user_id=user_id, group_id=group_id)
        .first()
    )
    if not existing:
        session.add(db.UserGroups(user_id=user_id, group_id=group_id))
    user = session.query(db.User).filter_by(id=user_id).first()
    if user is not None:
        user.organization_id = group_id
        session.add(user)
    session.commit()


def remove_user_from_group(user_id: int, group_id: int) -> None:
    """Remove a user from a group. Clears organization_id when it matches."""
    session = get_session()
    session.query(db.UserGroups).filter_by(
        user_id=user_id, group_id=group_id
    ).delete()
    user = session.query(db.User).filter_by(id=user_id).first()
    if user is not None and user.organization_id == group_id:
        user.organization_id = None
        session.add(user)
    session.commit()


def add_text_to_group(text_id: int, group_id: int) -> None:
    """Add a text (book) to a group. Idempotent."""
    session = get_session()
    existing = (
        session.query(db.TextGroups)
        .filter_by(group_id=group_id, text_id=text_id)
        .first()
    )
    if not existing:
        session.add(db.TextGroups(group_id=group_id, text_id=text_id))
        session.commit()


def remove_text_from_group(text_id: int, group_id: int) -> None:
    """Remove a text from a group."""
    session = get_session()
    session.query(db.TextGroups).filter_by(
        group_id=group_id, text_id=text_id
    ).delete()
    session.commit()


def all_texts_for_group_select() -> list[db.Text]:
    """All texts ordered by slug, for dropdowns (e.g. add book to group)."""
    session = get_session()
    return session.query(db.Text).order_by(db.Text.slug).all()


def all_users_for_group_select() -> list[db.User]:
    """All non-deleted, non-banned users ordered by username, for dropdowns."""
    session = get_session()
    return (
        session.query(db.User)
        .filter_by(is_deleted=False, is_banned=False)
        .order_by(db.User.username)
        .all()
    )


def _text_ids_in_any_group() -> set[int]:
    """Text ids that appear in at least one group (restricted texts)."""
    session = get_session()
    rows = session.query(db.TextGroups.text_id).distinct().all()
    return {r[0] for r in rows}


def user_can_view_text_legacy(user, text: db.Text) -> bool:
    """True if the user may view this library text (group access + admin).

    When group access is enforced: admins can view all; texts in no group are
    public; otherwise the user must be in at least one group that contains
    this text. Anonymous users can only view public (ungrouped) texts.
    """
    if getattr(user, "is_admin", False):
        return True
    restricted_ids = _text_ids_in_any_group()
    if text.id not in restricted_ids:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    session = get_session()
    row = (
        session.query(db.UserGroups)
        .join(db.TextGroups, db.UserGroups.group_id == db.TextGroups.group_id)
        .filter(
            db.UserGroups.user_id == user.id,
            db.TextGroups.text_id == text.id,
        )
        .first()
    )
    return row is not None


def _project_ids_in_any_group() -> set[int]:
    """Project ids that appear in at least one group (restricted projects)."""
    session = get_session()
    rows = session.query(db.ProjectGroups.project_id).distinct().all()
    return {r[0] for r in rows}


def user_can_view_project_legacy(user, project: db.Project) -> bool:
    """True if the user may view this proofing project (group access + admin).

    When group access is enforced: admins can view all; projects in no group are
    public; otherwise the user must be in at least one group that contains
    this project. Anonymous users can only view public (ungrouped) projects.
    """
    if getattr(project, "is_publicly_viewable", False):
        return True
    if getattr(user, "is_admin", False):
        return True
    restricted_ids = _project_ids_in_any_group()
    if project.id not in restricted_ids:
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    session = get_session()
    row = (
        session.query(db.UserGroups)
        .join(db.ProjectGroups, db.UserGroups.group_id == db.ProjectGroups.group_id)
        .filter(
            db.UserGroups.user_id == user.id,
            db.ProjectGroups.project_id == project.id,
        )
        .first()
    )
    return row is not None


def user_can_view_text(user, text: db.Text) -> bool:
    """Tenant-aware text visibility wrapper."""
    from kalanjiyam.utils.org_access import user_can_access_text

    return user_can_access_text(user, text)


def user_can_view_project(user, project: db.Project) -> bool:
    """Tenant-aware project visibility wrapper."""
    from kalanjiyam.utils.org_access import user_can_access_project

    return user_can_access_project(user, project)


def projects_in_group(
    group_id: int, page: int = 1, per_page: int = 20
) -> tuple[list[db.Project], int]:
    """Return a page of projects in the group and total count. 1-based page."""
    session = get_session()
    q_ = (
        session.query(db.Project)
        .join(db.ProjectGroups, db.Project.id == db.ProjectGroups.project_id)
        .filter(db.ProjectGroups.group_id == group_id)
    )
    total = q_.count()
    items = (
        q_.order_by(db.Project.slug)
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return items, total


def add_project_to_group(project_id: int, group_id: int) -> None:
    """Add a project to a group. Idempotent."""
    session = get_session()
    existing = (
        session.query(db.ProjectGroups)
        .filter_by(group_id=group_id, project_id=project_id)
        .first()
    )
    if not existing:
        session.add(db.ProjectGroups(group_id=group_id, project_id=project_id))
        session.commit()


def remove_project_from_group(project_id: int, group_id: int) -> None:
    """Remove a project from a group."""
    session = get_session()
    session.query(db.ProjectGroups).filter_by(
        group_id=group_id, project_id=project_id
    ).delete()
    session.commit()


def project_belongs_to_group(project_id: int, group_id: int) -> bool:
    session = get_session()
    return (
        session.query(db.ProjectGroups)
        .filter_by(group_id=group_id, project_id=project_id)
        .first()
        is not None
    )


def set_project_publicly_viewable(
    *, project_id: int, group_id: int, is_public: bool
) -> db.Project | None:
    """Set whether a book is visible on /books/ to everyone. Project must belong to group."""
    session = get_session()
    if not project_belongs_to_group(project_id, group_id):
        return None
    project = session.query(db.Project).filter_by(id=project_id).first()
    if project is None:
        return None
    project.is_publicly_viewable = is_public
    session.add(project)
    session.commit()
    return project


def all_projects_for_group_select() -> list[db.Project]:
    """All projects ordered by slug, for dropdowns (e.g. add project to group)."""
    session = get_session()
    return session.query(db.Project).order_by(db.Project.slug).all()
