"""Manages how we build and deploy assets.

Our CSS and JS stacks both support watcher programs that rebuild our assets
automatically whenever they detect filesystem changes on their input files. For
simplicity, we use those same watcher programs and avaid the Python `webassets`
stack. But since we avoid that stack, we must re-implement some parts of it.
"""

import functools
import hashlib
from pathlib import Path

from flask import url_for

STATIC_DIR = Path(__file__).parent.parent / "static"
assert STATIC_DIR.exists(), "Could not find static directory."


@functools.cache
def hashed_static(filename: str) -> str:
    """Add cache busting for asset URLs.

    We append a small hash prefix that represents the asset's content. The
    `functools.cache` decorator memoizes the function, and as a result, we
    hash the given asset file at most once per worker deploy.
    """
    asset_path = STATIC_DIR / filename
    try:
        content = asset_path.read_bytes()
    except FileNotFoundError:
        # If the content is missing, use an empty string to prevent a 500
        # error.
        content = b""
    hash_prefix = hashlib.md5(content).hexdigest()[:7]

    base_url = url_for("static", filename=filename)
    return f"{base_url}?h={hash_prefix}"


def get_page_image_filepath(project_slug: str, page_slug: str, org_slug: str | None = None) -> Path:
    """Get a local filesystem path for the given page image.

    With the local storage backend, this is the image's location on disk.
    With a remote backend (S3), the image is downloaded to a local cache
    first. Dual-read fallback (org_slug, open-tenant, legacy) is supported.
    """
    from kalanjiyam.utils.storage import get_storage, page_image_key, get_project_org_slug
    from kalanjiyam import queries as q, database as db

    storage = get_storage()

    if not org_slug:
        session = q.get_session()
        project_ = session.query(db.Project).filter_by(slug=project_slug).first()
        if project_:
            org_slug = get_project_org_slug(project_)
        else:
            org_slug = "open-tenant"

    primary_key = page_image_key(project_slug, page_slug, org_slug=org_slug)
    if storage.exists(primary_key):
        return storage.local_copy(primary_key)

    open_tenant_key = page_image_key(project_slug, page_slug, org_slug="open-tenant")
    if storage.exists(open_tenant_key):
        return storage.local_copy(open_tenant_key)

    legacy_key = f"projects/{project_slug}/pages/{page_slug}.jpg"
    if storage.exists(legacy_key):
        return storage.local_copy(legacy_key)

    return storage.local_copy(primary_key)
