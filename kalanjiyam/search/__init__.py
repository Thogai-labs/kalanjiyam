"""Full-text search over manuscripts, backed by OpenSearch (Lucene).

The package is organized as follows:

- :mod:`kalanjiyam.search.client` -- connection handling and health checks.
- :mod:`kalanjiyam.search.schema` -- index names, analyzers, and mappings.
- :mod:`kalanjiyam.search.acl` -- which indices a given user may search.
- :mod:`kalanjiyam.search.indexer` -- building documents and writing them.
- :mod:`kalanjiyam.search.query` -- building queries and parsing results.

Everything here is inert unless ``SEARCH_ENABLED`` is true. Callers should
treat search as a best-effort dependency: a search outage must never break
page saves, the catalog, or the reader.
"""
