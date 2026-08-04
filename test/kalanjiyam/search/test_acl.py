"""Visibility rules for search.

These are the tests that must not be wrong: a mistake here leaks one
organization's unpublished manuscripts to another.
"""

import pytest

import kalanjiyam.database as db
from kalanjiyam.enums import SiteRole
from kalanjiyam.queries import get_session
from kalanjiyam.search import acl, schema

PREFIX = "kalanjiyam"
PAGES_PATTERN = "kalanjiyam-pages-org-*"


class FakeUser:
    """Stands in for a logged-in user without touching the database."""

    def __init__(self, *, organization_id=None, roles=(), is_org_admin=False):
        self.is_authenticated = True
        self.organization_id = organization_id
        self._roles = set(roles)
        self.is_org_admin = is_org_admin

    def has_role(self, role):
        value = role.value if hasattr(role, "value") else role
        return value in self._roles


class FakeAnonymous:
    is_authenticated = False
    organization_id = None
    is_org_admin = False

    def has_role(self, role):
        return False


@pytest.fixture()
def app_ctx(flask_app):
    with flask_app.app_context():
        yield flask_app


def test_anonymous_sees_public_documents_only(app_ctx):
    scope = acl.search_scope(FakeAnonymous(), PREFIX)

    assert scope.indices == PAGES_PATTERN
    assert scope.filters == [{"term": {"is_public": True}}]
    assert scope.unrestricted is False


def test_org_member_sees_own_org_plus_public(app_ctx):
    scope = acl.search_scope(FakeUser(organization_id=7), PREFIX)

    assert scope.org_id == 7
    assert scope.unrestricted is False
    clauses = scope.filters[0]["bool"]["should"]
    assert {"term": {"group_ids": 7}} in clauses
    assert {"term": {"is_public": True}} in clauses
    assert scope.filters[0]["bool"]["minimum_should_match"] == 1


def test_org_member_filter_excludes_other_orgs(app_ctx):
    """The filter must not admit a non-public document from another org."""
    scope = acl.search_scope(FakeUser(organization_id=7), PREFIX)
    clauses = scope.filters[0]["bool"]["should"]

    # The only ways in are "belongs to org 7" and "is public".
    assert len(clauses) == 2
    assert {"term": {"group_ids": 8}} not in clauses


def test_super_admin_is_unrestricted(app_ctx):
    user = FakeUser(organization_id=7, roles={SiteRole.SUPER_ADMIN.value})
    scope = acl.search_scope(user, PREFIX)

    assert scope.unrestricted is True
    assert scope.filters == []


def test_legacy_admin_is_not_unrestricted(app_ctx):
    """Only super_admin bypasses tenancy; the legacy `admin` role does not."""
    user = FakeUser(organization_id=7, roles={SiteRole.ADMIN.value})
    scope = acl.search_scope(user, PREFIX)

    assert scope.unrestricted is False
    assert scope.filters


def test_user_without_organization_falls_back_to_open_tenant(app_ctx):
    """`user_organization_id` resolves a NULL organization to open-tenant.

    The lookup creates the group on demand, so query for it only afterwards.
    """
    scope = acl.search_scope(FakeUser(organization_id=None), PREFIX)

    session = get_session()
    open_tenant = session.query(db.Group).filter_by(slug="open-tenant").first()
    assert open_tenant is not None

    assert scope.org_id == open_tenant.id
    assert {"term": {"group_ids": open_tenant.id}} in scope.filters[0]["bool"]["should"]


def test_scope_targets_the_alias_wildcard_not_store_indices(app_ctx):
    """Searches must never reach a store index directly."""
    scope = acl.search_scope(FakeAnonymous(), PREFIX)
    head = scope.indices.rstrip("*")

    assert not schema.store_index(PREFIX, schema.PAGES, 1, 1).startswith(head)


def test_projects_scope_targets_the_projects_pattern(app_ctx):
    scope = acl.search_scope(FakeAnonymous(), PREFIX, schema.PROJECTS)
    assert scope.indices == "kalanjiyam-projects-org-*"


def test_manageable_orgs_for_super_admin(app_ctx):
    user = FakeUser(organization_id=2, roles={SiteRole.SUPER_ADMIN.value})
    assert acl.manageable_org_ids(user, [3, 1, 2]) == [1, 2, 3]


def test_manageable_orgs_for_org_admin_is_own_org_only(app_ctx):
    user = FakeUser(organization_id=2, is_org_admin=True)
    assert acl.manageable_org_ids(user, [1, 2, 3]) == [2]


def test_manageable_orgs_for_plain_user_is_empty(app_ctx):
    assert acl.manageable_org_ids(FakeUser(organization_id=2), [1, 2, 3]) == []
    assert acl.manageable_org_ids(FakeAnonymous(), [1, 2, 3]) == []
