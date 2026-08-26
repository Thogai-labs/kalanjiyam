"""Deciding what a given user is allowed to search.

Indices are partitioned per organization, but a search still has to span more
than one org: a project flagged ``is_publicly_viewable`` is readable by
everyone, including anonymous visitors, and those public projects live in
their owning org's index (see :mod:`kalanjiyam.search.schema`).

So the query targets the org-alias wildcard and carries a document-level
filter that reproduces the rule:

    a document is visible if it belongs to my organization, or it is public

This mirrors :func:`kalanjiyam.utils.org_access.user_can_access_project` for
the org-wise model. Every visibility decision for search lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kalanjiyam.search import schema
from kalanjiyam.utils.admin_access import is_platform_super_admin
from kalanjiyam.utils.org_access import user_organization_id, user_organization_ids


@dataclass(frozen=True)
class SearchScope:
    """The index targets and filter clauses for one user's searches."""

    #: Index expression passed to OpenSearch (an alias wildcard).
    indices: str
    #: Filter clauses ANDed into the query's ``bool.filter``.
    filters: list[dict] = field(default_factory=list)
    #: The user's own organization, if any. Useful for dashboards.
    org_id: int | None = None
    #: True when the user may see every document in every org.
    unrestricted: bool = False


def search_scope(user, prefix: str, kind: str = schema.PAGES) -> SearchScope:
    """Return the search scope for ``user``.

    - Super admins search every org index, unfiltered.
    - Signed-in users see their own assigned orgs' documents plus public documents
      from any org.
    - Anonymous visitors see public documents only.
    """
    indices = schema.search_pattern(prefix, kind)

    if is_platform_super_admin(user):
        return SearchScope(
            indices=indices,
            filters=[],
            org_id=user_organization_id(user),
            unrestricted=True,
        )

    if not getattr(user, "is_authenticated", False):
        return SearchScope(indices=indices, filters=[public_only_filter()])

    user_orgs = sorted(user_organization_ids(user))
    if not user_orgs:
        # Authenticated but with no resolvable organization (open-tenant
        # lookup failed). Fall back to public documents rather than guessing.
        return SearchScope(indices=indices, filters=[public_only_filter()])

    return SearchScope(
        indices=indices,
        filters=[own_orgs_or_public_filter(user_orgs)],
        org_id=user_organization_id(user),
    )


def public_only_filter() -> dict:
    return {"term": {"is_public": True}}


def own_org_or_public_filter(org_id: int) -> dict:
    return {
        "bool": {
            "should": [
                {"term": {"group_ids": org_id}},
                {"term": {"is_public": True}},
            ],
            "minimum_should_match": 1,
        }
    }


def own_orgs_or_public_filter(org_ids: list[int]) -> dict:
    if len(org_ids) == 1:
        return own_org_or_public_filter(org_ids[0])
    return {
        "bool": {
            "should": [
                {"terms": {"group_ids": org_ids}},
                {"term": {"is_public": True}},
            ],
            "minimum_should_match": 1,
        }
    }


def manageable_org_ids(user, all_org_ids) -> list[int]:
    """Organizations whose index this user may rebuild or drop.

    Super admins manage every organization; master users manage all their assigned orgs;
    org admins manage exactly their own.
    """
    if is_platform_super_admin(user):
        return sorted(all_org_ids)
    if getattr(user, "is_master_user", False):
        return sorted(user_organization_ids(user))
    org_id = getattr(user, "organization_id", None)
    if org_id and getattr(user, "is_org_admin", False):
        return [org_id]
    return []
