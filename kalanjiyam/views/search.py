"""Public full-text search across manuscripts.

Backed by OpenSearch when ``SEARCH_ENABLED`` is on. When it is off, or the
cluster cannot be reached, the page falls back to a plain SQL match over
project metadata so the site keeps working -- degraded, but never broken.
"""

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user

from kalanjiyam.search import query as search_query
from kalanjiyam.search.client import is_enabled

bp = Blueprint("search", __name__)

#: Guard against absurd page numbers in hand-edited URLs.
MAX_PAGE = 500


def _parse_request() -> search_query.SearchRequest:
    per_page = int(current_app.config.get("SEARCH_RESULTS_PER_PAGE") or 20)

    view = request.args.get("view", search_query.VIEW_GROUPED)
    if view not in search_query.VIEWS:
        view = search_query.VIEW_GROUPED

    try:
        page = max(1, min(MAX_PAGE, int(request.args.get("page", 1))))
    except (TypeError, ValueError):
        page = 1

    try:
        project_id = int(request.args["book"]) if request.args.get("book") else None
    except (TypeError, ValueError):
        project_id = None

    return search_query.SearchRequest(
        q=(request.args.get("q") or "").strip(),
        view=view,
        page=page,
        per_page=per_page,
        advanced=request.args.get("advanced") in ("1", "true", "on"),
        project_id=project_id,
        author=request.args.get("author") or None,
        lang=request.args.get("lang") or None,
        genre=request.args.get("genre") or None,
    )


def _fallback_results(q: str):
    """Match project titles and authors in SQL, with no page-level search.

    Used when search is switched off or the cluster is unreachable. Reuses the
    catalog's own visibility filter so the fallback can never show more than
    the catalog page would.
    """
    from kalanjiyam.views.public.books import get_public_projects

    if not q:
        return []

    needle = q.lower()
    matches = []
    for project in get_public_projects():
        haystack = " ".join(
            filter(None, [project.display_title, project.author, project.print_title])
        ).lower()
        if needle in haystack:
            matches.append(project)
    matches.sort(key=lambda p: p.display_title)
    return matches


@bp.route("/")
def index():
    """Search results, grouped by book or flat by page."""
    req = _parse_request()

    if not req.q:
        return render_template(
            "search/index.html", req=req, results=None, degraded=not is_enabled()
        )

    if not is_enabled():
        return render_template(
            "search/index.html",
            req=req,
            results=None,
            degraded=True,
            fallback_projects=_fallback_results(req.q),
        )

    results = search_query.search(current_user, req)
    degraded = bool(results.error)
    return render_template(
        "search/index.html",
        req=req,
        results=results,
        degraded=degraded,
        fallback_projects=_fallback_results(req.q) if degraded else None,
    )


@bp.route("/suggest")
def suggest():
    """Title/author completions for the home-page search box."""
    q = (request.args.get("q") or "").strip()
    if not q or not is_enabled():
        return jsonify([])
    return jsonify(search_query.suggest(current_user, q))
