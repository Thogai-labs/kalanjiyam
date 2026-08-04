"""Connection handling for the OpenSearch cluster.

Search is an optional dependency. Every entry point here degrades to "search
is unavailable" rather than raising, so that a cluster outage shows up as a
banner on the search page instead of a 500 on the home page.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from flask import current_app

LOG = logging.getLogger(__name__)

#: Cached client, keyed by the connection settings that produced it. The
#: Flask app config is stable within a process, but Celery workers and the
#: CLI build their own app contexts, so we key on the settings rather than
#: assuming a single global.
_CLIENTS: dict[tuple, Any] = {}


class SearchUnavailableError(RuntimeError):
    """Raised when a caller needs the cluster but it cannot be reached."""


@dataclass(frozen=True)
class SearchSettings:
    """Connection settings, read once from the Flask config."""

    enabled: bool
    url: str
    user: str
    password: str
    index_prefix: str
    bulk_chunk_size: int
    results_per_page: int
    request_timeout: int

    @property
    def cache_key(self) -> tuple:
        return (self.url, self.user, self.password, self.request_timeout)


def get_settings() -> SearchSettings:
    """Read search settings from the active Flask app config."""
    cfg = current_app.config
    return SearchSettings(
        enabled=bool(cfg.get("SEARCH_ENABLED")),
        url=cfg.get("OPENSEARCH_URL") or "http://localhost:9200",
        user=cfg.get("OPENSEARCH_USER") or "",
        password=cfg.get("OPENSEARCH_PASSWORD") or "",
        index_prefix=cfg.get("SEARCH_INDEX_PREFIX") or "kalanjiyam",
        bulk_chunk_size=int(cfg.get("SEARCH_BULK_CHUNK_SIZE") or 500),
        results_per_page=int(cfg.get("SEARCH_RESULTS_PER_PAGE") or 20),
        request_timeout=int(cfg.get("SEARCH_REQUEST_TIMEOUT") or 30),
    )


def is_enabled() -> bool:
    """True if search is switched on for this deployment."""
    return bool(current_app.config.get("SEARCH_ENABLED"))


def get_client(*, required: bool = True):
    """Return an OpenSearch client, or ``None`` when search is disabled.

    :param required: if True, raise :class:`SearchUnavailableError` when
        search is disabled or the ``opensearch-py`` package is missing.
    """
    settings = get_settings()
    if not settings.enabled:
        if required:
            raise SearchUnavailableError("SEARCH_ENABLED is false")
        return None

    key = settings.cache_key
    if key in _CLIENTS:
        return _CLIENTS[key]

    try:
        from opensearchpy import OpenSearch
    except ImportError as e:  # pragma: no cover - depends on the environment
        if required:
            raise SearchUnavailableError(
                "opensearch-py is not installed; run `make install`"
            ) from e
        LOG.warning("opensearch-py is not installed; search is unavailable")
        return None

    kwargs: dict[str, Any] = {
        "hosts": [settings.url],
        "timeout": settings.request_timeout,
        "retry_on_timeout": True,
        "max_retries": 2,
    }
    if settings.user:
        kwargs["http_auth"] = (settings.user, settings.password)
    if settings.url.startswith("https://"):
        # Self-hosted clusters commonly use the bundled self-signed cert.
        kwargs["verify_certs"] = False
        kwargs["ssl_show_warn"] = False

    client = OpenSearch(**kwargs)
    _CLIENTS[key] = client
    return client


def reset_client_cache() -> None:
    """Drop cached clients. Used by tests and after a config change."""
    _CLIENTS.clear()


def health() -> dict[str, Any]:
    """Describe cluster reachability for the admin dashboard.

    Never raises: the dashboard must render even when the cluster is down.
    """
    if not is_enabled():
        return {"enabled": False, "reachable": False, "status": "disabled"}

    try:
        client = get_client()
    except SearchUnavailableError as e:
        # The client could not even be built -- a packaging or config problem,
        # not a down cluster. Say so, or the reader goes hunting the network
        # for a fault that is on this machine.
        LOG.warning("OpenSearch client unavailable: %s", e)
        return {
            "enabled": True,
            "reachable": False,
            "status": "misconfigured",
            "error": str(e),
        }

    try:
        info = client.cluster.health()
        return {
            "enabled": True,
            "reachable": True,
            "status": info.get("status", "unknown"),
            "number_of_nodes": info.get("number_of_nodes"),
            "active_shards": info.get("active_shards"),
        }
    except Exception as e:
        LOG.warning("OpenSearch health check failed: %s", e)
        return {
            "enabled": True,
            "reachable": False,
            "status": "unreachable",
            "error": str(e),
        }
