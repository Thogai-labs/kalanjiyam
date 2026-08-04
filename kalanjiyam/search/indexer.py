"""Building search documents and writing them to OpenSearch."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import kalanjiyam.database as db
from kalanjiyam.search import schema
from kalanjiyam.search.client import get_client, get_settings
from kalanjiyam.utils.page_document import PageDocument
from kalanjiyam.utils.text_utils import normalize_unicode_text

LOG = logging.getLogger(__name__)

#: Revisions are fetched in batches of this size when their text is needed,
#: so a project with thousands of pages never loads all of its content at once.
_REVISION_FETCH_SIZE = 200


# Document construction
# ---------------------


def project_group_ids(project: db.Project) -> list[int]:
    """Organizations that own this project.

    Every write path assigns exactly one group, but the schema permits several
    and the index handles the general case.
    """
    return sorted({g.id for g in (project.groups or [])})


def project_dominant_language(session, project_id: int) -> str | None:
    """Best-effort source language for a project.

    There is no language column on projects or pages, so fall back to the most
    common source language across its translations.
    """
    rows = (
        session.query(db.Translation.source_language)
        .join(db.Page, db.Translation.page_id == db.Page.id)
        .filter(db.Page.project_id == project_id)
        .all()
    )
    langs = Counter(r[0] for r in rows if r[0])
    if not langs:
        return None
    return langs.most_common(1)[0][0]


def _page_language(doc: PageDocument, fallback: str | None) -> str | None:
    langs = Counter(b.language for b in doc.blocks if b.language)
    if langs:
        return langs.most_common(1)[0][0]
    return fallback


def build_page_doc(
    page: db.Page,
    project: db.Project,
    revision: db.Revision,
    *,
    group_ids: list[int],
    fallback_lang: str | None = None,
) -> dict[str, Any]:
    """Build the indexed document for one page."""
    doc = PageDocument.from_dict(revision.document)
    text = doc.to_plain_text() if doc.blocks else ""
    if not text:
        text = revision.content or ""
    text = normalize_unicode_text(text)
    lang = _page_language(doc, fallback_lang)

    source = {
        "content": text,
        # Stemmed English is worth a second field; no other language in the
        # corpus has an analyzer that would benefit.
        "content_en": text if lang == "en" else None,
        "lang": lang,
        "page_id": page.id,
        "page_slug": page.slug,
        "page_order": page.order,
        "project_id": project.id,
        "project_slug": project.slug,
        "project_title": project.display_title,
        "project_author": project.author or None,
        "genre": project.genre.name if project.genre else None,
        "revision_id": revision.id,
        "revision_created": revision.created,
        "status": revision.status.name if revision.status else None,
        "indexed_at": datetime.utcnow(),
        "group_ids": group_ids,
        "is_public": bool(project.is_publicly_viewable),
        "creator_id": project.creator_id,
    }
    return {k: v for k, v in source.items() if v is not None}


def build_project_doc(
    project: db.Project,
    *,
    group_ids: list[int],
    page_count: int,
    ocr_page_count: int,
) -> dict[str, Any]:
    """Build the indexed document for one project's metadata."""
    title = normalize_unicode_text(project.display_title)
    source = {
        "project_id": project.id,
        "slug": project.slug,
        "display_title": title,
        "print_title": normalize_unicode_text(project.print_title) or None,
        "author": normalize_unicode_text(project.author) or None,
        "editor": normalize_unicode_text(project.editor) or None,
        "publisher": normalize_unicode_text(project.publisher) or None,
        "publication_year": project.publication_year or None,
        "description": normalize_unicode_text(project.description) or None,
        "genre": project.genre.name if project.genre else None,
        "page_count": page_count,
        "ocr_page_count": ocr_page_count,
        "created_at": project.created_at,
        "indexed_at": datetime.utcnow(),
        "group_ids": group_ids,
        "is_public": bool(project.is_publicly_viewable),
        "creator_id": project.creator_id,
    }
    # display_title carries a `suggest` completion sub-field; OpenSearch
    # derives it from this same string, so nothing extra is sent.
    return {k: v for k, v in source.items() if v is not None}


# Reading the corpus
# ------------------


def latest_revision_ids(session, project_id: int) -> dict[int, int]:
    """Map page id -> id of its newest revision.

    Ordering matches ``Page.revisions[-1]``, which is what the public reader
    renders, so a search snippet can never disagree with the page a reader
    lands on.
    """
    rows = (
        session.query(db.Revision.page_id, db.Revision.id)
        .filter(db.Revision.project_id == project_id)
        .order_by(db.Revision.created.asc(), db.Revision.id.asc())
        .all()
    )
    latest: dict[int, int] = {}
    for page_id, revision_id in rows:
        latest[page_id] = revision_id
    return latest


def iter_page_docs(session, project: db.Project) -> Iterator[tuple[str, dict]]:
    """Yield ``(doc_id, source)`` for every page of a project that has text.

    Pages with no revisions are skipped: there is nothing to search, and
    indexing an empty document would pollute result counts.
    """
    group_ids = project_group_ids(project)
    if not group_ids:
        return
    fallback_lang = project_dominant_language(session, project.id)

    latest = latest_revision_ids(session, project.id)
    if not latest:
        return

    pages_by_id = {p.id: p for p in project.pages}
    revision_ids = [rid for pid, rid in latest.items() if pid in pages_by_id]

    for start in range(0, len(revision_ids), _REVISION_FETCH_SIZE):
        chunk = revision_ids[start : start + _REVISION_FETCH_SIZE]
        revisions = (
            session.query(db.Revision).filter(db.Revision.id.in_(chunk)).all()
        )
        for revision in revisions:
            page = pages_by_id.get(revision.page_id)
            if page is None:
                continue
            source = build_page_doc(
                page,
                project,
                revision,
                group_ids=group_ids,
                fallback_lang=fallback_lang,
            )
            yield schema.page_doc_id(page.id), source
            # Revision bodies are the largest objects in a rebuild. Drop each
            # one from the identity map as soon as it is indexed so a project
            # with thousands of pages does not accumulate them all.
            session.expunge(revision)


def project_page_counts(session, project_id: int) -> tuple[int, int]:
    """Return ``(total_pages, pages_with_a_revision)``."""
    total = (
        session.query(db.Page).filter(db.Page.project_id == project_id).count()
    )
    ocr = (
        session.query(db.Revision.page_id)
        .filter(db.Revision.project_id == project_id)
        .distinct()
        .count()
    )
    return total, ocr


def build_project_source(session, project: db.Project) -> dict[str, Any] | None:
    group_ids = project_group_ids(project)
    if not group_ids:
        return None
    total, ocr = project_page_counts(session, project.id)
    return build_project_doc(
        project, group_ids=group_ids, page_count=total, ocr_page_count=ocr
    )


def projects_for_org(session, group_id: int) -> list[db.Project]:
    return (
        session.query(db.Project)
        .join(db.ProjectGroups, db.ProjectGroups.project_id == db.Project.id)
        .filter(db.ProjectGroups.group_id == group_id)
        .all()
    )


def all_group_ids(session) -> list[int]:
    rows = session.query(db.ProjectGroups.group_id).distinct().all()
    return sorted(r[0] for r in rows)


def ungrouped_project_count(session) -> int:
    """Projects that belong to no organization and so are not indexed."""
    grouped = session.query(db.ProjectGroups.project_id).distinct().subquery()
    return (
        session.query(db.Project)
        .filter(~db.Project.id.in_(grouped.select()))
        .count()
    )


# Index lifecycle
# ---------------


def _next_version(client, prefix: str, kind: str, group_id: int) -> int:
    pattern = schema.store_pattern(prefix, kind, group_id)
    try:
        existing = client.indices.get(index=pattern, ignore_unavailable=True) or {}
    except Exception:
        existing = {}
    versions = []
    for name in existing:
        parsed = schema.parse_store_index(prefix, name)
        if parsed:
            versions.append(parsed[2])
    return (max(versions) + 1) if versions else 1


def current_store_indices(client, prefix: str, kind: str, group_id: int) -> list[str]:
    """Concrete indices the alias currently points at."""
    name = schema.alias(prefix, kind, group_id)
    try:
        mapping = client.indices.get_alias(name=name, ignore_unavailable=True) or {}
    except Exception:
        return []
    return sorted(mapping.keys())


def create_store_index(client, prefix: str, kind: str, group_id: int) -> str:
    """Create a fresh store index and return its name."""
    version = _next_version(client, prefix, kind, group_id)
    name = schema.store_index(prefix, kind, group_id, version)
    client.indices.create(index=name, body=schema.index_body(kind))
    return name


def swap_alias(client, prefix: str, kind: str, group_id: int, new_index: str) -> list[str]:
    """Point the alias at ``new_index``. Returns the indices it left behind."""
    name = schema.alias(prefix, kind, group_id)
    old = current_store_indices(client, prefix, kind, group_id)
    actions = [{"remove": {"index": i, "alias": name}} for i in old if i != new_index]
    actions.append({"add": {"index": new_index, "alias": name}})
    client.indices.update_aliases(body={"actions": actions})
    return [i for i in old if i != new_index]


def ensure_org_indices(client, prefix: str, group_id: int) -> None:
    """Create empty indices and aliases for an org if they do not exist yet."""
    for kind in schema.DOC_KINDS:
        if current_store_indices(client, prefix, kind, group_id):
            continue
        index = create_store_index(client, prefix, kind, group_id)
        swap_alias(client, prefix, kind, group_id, index)


def drop_org_indices(client, prefix: str, group_id: int) -> list[str]:
    """Delete every index for an org, alias included. Returns names removed."""
    removed = []
    for kind in schema.DOC_KINDS:
        pattern = schema.store_pattern(prefix, kind, group_id)
        try:
            existing = client.indices.get(index=pattern, ignore_unavailable=True) or {}
        except Exception:
            existing = {}
        for name in existing:
            client.indices.delete(index=name, ignore_unavailable=True)
            removed.append(name)
    return removed


# Writing
# -------


def _bulk(client, actions: Iterator[dict], chunk_size: int) -> tuple[int, int]:
    """Stream actions to the cluster. Returns ``(succeeded, failed)``."""
    from opensearchpy.helpers import streaming_bulk

    succeeded = failed = 0
    for ok, item in streaming_bulk(
        client,
        actions,
        chunk_size=chunk_size,
        raise_on_error=False,
        raise_on_exception=False,
        max_retries=2,
    ):
        if ok:
            succeeded += 1
        else:
            failed += 1
            LOG.warning("Search index write failed: %s", item)
    return succeeded, failed


def index_org(
    client,
    session,
    prefix: str,
    group_id: int,
    *,
    target_pages: str | None = None,
    target_projects: str | None = None,
    chunk_size: int = 500,
    on_progress: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """Write every document for one organization.

    ``target_pages`` / ``target_projects`` name the indices to write to. They
    default to the live aliases; a rebuild passes the new store indices so the
    alias can be swapped only once the build succeeds.
    """
    pages_index = target_pages or schema.alias(prefix, schema.PAGES, group_id)
    projects_index = target_projects or schema.alias(prefix, schema.PROJECTS, group_id)
    projects = projects_for_org(session, group_id)

    counter = {"n": 0}

    def actions() -> Iterator[dict]:
        for project in projects:
            if should_stop and should_stop():
                return
            source = build_project_source(session, project)
            if source is not None:
                yield {
                    "_op_type": "index",
                    "_index": projects_index,
                    "_id": schema.project_doc_id(project.id),
                    "_source": source,
                }
            for doc_id, page_source in iter_page_docs(session, project):
                yield {
                    "_op_type": "index",
                    "_index": pages_index,
                    "_id": doc_id,
                    "_source": page_source,
                }
                counter["n"] += 1
                if on_progress and counter["n"] % chunk_size == 0:
                    on_progress(counter["n"])

    result = _bulk(client, actions(), chunk_size)
    if on_progress:
        on_progress(counter["n"])
    return result


def rebuild_org(
    client,
    session,
    prefix: str,
    group_id: int,
    *,
    chunk_size: int = 500,
    on_progress: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> tuple[int, int]:
    """Rebuild an org's indices from scratch with no search downtime.

    Builds into fresh store indices, swaps the aliases only on success, then
    deletes what the aliases used to point at. A failure part-way through
    leaves the live indices untouched.
    """
    new_pages = create_store_index(client, prefix, schema.PAGES, group_id)
    new_projects = create_store_index(client, prefix, schema.PROJECTS, group_id)

    try:
        succeeded, failed = index_org(
            client,
            session,
            prefix,
            group_id,
            target_pages=new_pages,
            target_projects=new_projects,
            chunk_size=chunk_size,
            on_progress=on_progress,
            should_stop=should_stop,
        )
    except Exception:
        client.indices.delete(index=new_pages, ignore_unavailable=True)
        client.indices.delete(index=new_projects, ignore_unavailable=True)
        raise

    if should_stop and should_stop():
        client.indices.delete(index=new_pages, ignore_unavailable=True)
        client.indices.delete(index=new_projects, ignore_unavailable=True)
        return succeeded, failed

    client.indices.refresh(index=f"{new_pages},{new_projects}", ignore_unavailable=True)
    retired = swap_alias(client, prefix, schema.PAGES, group_id, new_pages)
    retired += swap_alias(client, prefix, schema.PROJECTS, group_id, new_projects)
    for name in retired:
        client.indices.delete(index=name, ignore_unavailable=True)
    return succeeded, failed


def index_project(session, project: db.Project, *, client=None, prefix: str | None = None) -> int:
    """Upsert one project and all of its pages into the live aliases.

    Also removes the project's documents from any org index it no longer
    belongs to, so that moving a project between organizations does not leave
    a searchable copy behind.
    """
    settings = get_settings()
    client = client or get_client()
    prefix = prefix or settings.index_prefix

    group_ids = project_group_ids(project)
    delete_project_docs(client, prefix, project.id, keep_group_ids=group_ids)
    if not group_ids:
        return 0

    written = 0
    for group_id in group_ids:
        ensure_org_indices(client, prefix, group_id)
        pages_index = schema.alias(prefix, schema.PAGES, group_id)
        projects_index = schema.alias(prefix, schema.PROJECTS, group_id)

        def actions(group_id=group_id, pages_index=pages_index, projects_index=projects_index):
            source = build_project_source(session, project)
            if source is not None:
                yield {
                    "_op_type": "index",
                    "_index": projects_index,
                    "_id": schema.project_doc_id(project.id),
                    "_source": source,
                }
            for doc_id, page_source in iter_page_docs(session, project):
                yield {
                    "_op_type": "index",
                    "_index": pages_index,
                    "_id": doc_id,
                    "_source": page_source,
                }

        succeeded, _failed = _bulk(client, actions(), settings.bulk_chunk_size)
        written += succeeded
    return written


def index_page(session, page: db.Page, *, client=None, prefix: str | None = None) -> bool:
    """Upsert a single page. Returns True if a document was written."""
    settings = get_settings()
    client = client or get_client()
    prefix = prefix or settings.index_prefix

    project = page.project
    group_ids = project_group_ids(project)
    if not group_ids or not page.revisions:
        return False

    revision = page.revisions[-1]
    fallback_lang = project_dominant_language(session, project.id)
    source = build_page_doc(
        page, project, revision, group_ids=group_ids, fallback_lang=fallback_lang
    )
    for group_id in group_ids:
        ensure_org_indices(client, prefix, group_id)
        client.index(
            index=schema.alias(prefix, schema.PAGES, group_id),
            id=schema.page_doc_id(page.id),
            body=source,
        )
    return True


def delete_project_docs(
    client, prefix: str, project_id: int, *, keep_group_ids: list[int] | None = None
) -> None:
    """Remove a project and its pages from every org index.

    ``keep_group_ids`` spares the indices the project still belongs to, which
    is what makes a re-index after a group change safe to run.
    """
    keep = set(keep_group_ids or [])
    for kind in schema.DOC_KINDS:
        pattern = schema.search_pattern(prefix, kind)
        try:
            aliases = client.indices.get_alias(name=pattern) or {}
        except Exception:
            continue
        for store_index, meta in aliases.items():
            names = list((meta.get("aliases") or {}).keys())
            group_ids = {
                parsed[1]
                for parsed in (schema.parse_alias(prefix, n) for n in names)
                if parsed
            }
            if group_ids and group_ids <= keep:
                continue
            try:
                client.delete_by_query(
                    index=store_index,
                    body={"query": {"term": {"project_id": project_id}}},
                    conflicts="proceed",
                    refresh=True,
                )
            except Exception as e:
                LOG.warning(
                    "Failed to delete project %s from %s: %s", project_id, store_index, e
                )


def delete_page_doc(client, prefix: str, page_id: int) -> None:
    """Remove one page from every org index."""
    for kind in (schema.PAGES,):
        try:
            client.delete_by_query(
                index=schema.search_pattern(prefix, kind),
                body={"query": {"term": {"page_id": page_id}}},
                conflicts="proceed",
                ignore_unavailable=True,
                refresh=True,
            )
        except Exception as e:
            LOG.warning("Failed to delete page %s from the index: %s", page_id, e)


# Reconciliation
# --------------


def index_drift(client, session, prefix: str, group_id: int) -> dict[str, Any]:
    """Compare the index against the database for one organization.

    Reports pages that are missing, stale (indexed from an older revision), or
    orphaned (indexed but no longer in the database).
    """
    expected: dict[int, int] = {}
    for project in projects_for_org(session, group_id):
        page_ids = {p.id for p in project.pages}
        for page_id, revision_id in latest_revision_ids(session, project.id).items():
            if page_id in page_ids:
                expected[page_id] = revision_id

    indexed: dict[int, int] = {}
    index = schema.alias(prefix, schema.PAGES, group_id)
    try:
        from opensearchpy.helpers import scan

        for hit in scan(
            client,
            index=index,
            query={"query": {"match_all": {}}, "_source": ["page_id", "revision_id"]},
            preserve_order=False,
        ):
            source = hit.get("_source") or {}
            if "page_id" in source:
                indexed[source["page_id"]] = source.get("revision_id")
    except Exception as e:
        LOG.warning("Could not scan %s for drift: %s", index, e)
        return {"available": False, "error": str(e)}

    missing = [p for p in expected if p not in indexed]
    stale = [p for p, r in expected.items() if p in indexed and indexed[p] != r]
    orphaned = [p for p in indexed if p not in expected]
    return {
        "available": True,
        "expected": len(expected),
        "indexed": len(indexed),
        "missing": len(missing),
        "stale": len(stale),
        "orphaned": len(orphaned),
        "missing_ids": missing,
        "stale_ids": stale,
        "orphaned_ids": orphaned,
    }


def sync_org(
    client,
    session,
    prefix: str,
    group_id: int,
    *,
    chunk_size: int = 500,
) -> dict[str, Any]:
    """Bring one org's index back in line with the database.

    Cheaper than a rebuild when only a handful of pages have drifted, which is
    the normal case if an incremental hook was missed.
    """
    ensure_org_indices(client, prefix, group_id)
    drift = index_drift(client, session, prefix, group_id)
    if not drift.get("available"):
        return drift

    to_write = set(drift["missing_ids"]) | set(drift["stale_ids"])
    orphaned = drift["orphaned_ids"]

    written = 0
    if to_write:
        pages = (
            session.query(db.Page).filter(db.Page.id.in_(list(to_write))).all()
        )
        pages_index = schema.alias(prefix, schema.PAGES, group_id)
        lang_cache: dict[int, str | None] = {}

        def actions():
            for page in pages:
                if not page.revisions:
                    continue
                project = page.project
                group_ids = project_group_ids(project)
                if not group_ids:
                    continue
                if project.id not in lang_cache:
                    lang_cache[project.id] = project_dominant_language(
                        session, project.id
                    )
                yield {
                    "_op_type": "index",
                    "_index": pages_index,
                    "_id": schema.page_doc_id(page.id),
                    "_source": build_page_doc(
                        page,
                        project,
                        page.revisions[-1],
                        group_ids=group_ids,
                        fallback_lang=lang_cache[project.id],
                    ),
                }

        written, _failed = _bulk(client, actions(), chunk_size)

    deleted = 0
    if orphaned:
        index = schema.alias(prefix, schema.PAGES, group_id)
        client.delete_by_query(
            index=index,
            body={"query": {"terms": {"page_id": orphaned}}},
            conflicts="proceed",
            refresh=True,
        )
        deleted = len(orphaned)

    # Project metadata is small enough to refresh wholesale.
    projects_index = schema.alias(prefix, schema.PROJECTS, group_id)

    def project_actions():
        for project in projects_for_org(session, group_id):
            source = build_project_source(session, project)
            if source is not None:
                yield {
                    "_op_type": "index",
                    "_index": projects_index,
                    "_id": schema.project_doc_id(project.id),
                    "_source": source,
                }

    _bulk(client, project_actions(), chunk_size)

    return {**drift, "written": written, "deleted": deleted}


def org_stats(client, prefix: str, group_id: int) -> dict[str, Any]:
    """Document counts and store size for one org, for the dashboard."""
    stats: dict[str, Any] = {"group_id": group_id}
    for kind in schema.DOC_KINDS:
        name = schema.alias(prefix, kind, group_id)
        try:
            count = client.count(index=name, ignore_unavailable=True).get("count", 0)
        except Exception:
            count = 0
        size = 0
        try:
            # No ignore_unavailable here: indices.stats() rejects it as an
            # unknown query param, and a TypeError would be swallowed below
            # and silently report every index as 0 bytes. A missing index
            # raises instead, which the handler covers.
            raw = client.indices.stats(index=name)
            size = (
                raw.get("_all", {})
                .get("primaries", {})
                .get("store", {})
                .get("size_in_bytes", 0)
            )
        except Exception:
            size = 0
        stats[f"{kind}_count"] = count
        stats[f"{kind}_size_bytes"] = size
        stats[f"{kind}_indices"] = current_store_indices(client, prefix, kind, group_id)
    return stats
