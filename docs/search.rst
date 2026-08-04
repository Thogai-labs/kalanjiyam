Full-text search
================

Kalanjiyam's public search at ``/search`` is backed by OpenSearch, an Apache-2.0
distribution of Apache Lucene. It searches the text of every OCR'd manuscript
page, not just book titles, and returns highlighted snippets that deep-link to
the exact page in the reader.

Search is **optional**. With ``SEARCH_ENABLED=false`` the application never
contacts the cluster, and ``/search`` falls back to a plain SQL match over book
titles and authors. Nothing else in the product depends on it.


Configuration
-------------

Set these in ``.env``:

+------------------------------+---------------------------+------------------------------------------+
| Variable                     | Default                   | Meaning                                  |
+==============================+===========================+==========================================+
| ``SEARCH_ENABLED``           | ``false``                 | Master switch                            |
+------------------------------+---------------------------+------------------------------------------+
| ``OPENSEARCH_URL``           | ``http://localhost:9200`` | Cluster endpoint                         |
+------------------------------+---------------------------+------------------------------------------+
| ``OPENSEARCH_USER``          | empty                     | Only if the security plugin is on        |
+------------------------------+---------------------------+------------------------------------------+
| ``OPENSEARCH_PASSWORD``      | empty                     |                                          |
+------------------------------+---------------------------+------------------------------------------+
| ``SEARCH_INDEX_PREFIX``      | ``kalanjiyam``            | Prefix for every index name              |
+------------------------------+---------------------------+------------------------------------------+
| ``SEARCH_BULK_CHUNK_SIZE``   | ``500``                   | Documents per bulk request               |
+------------------------------+---------------------------+------------------------------------------+
| ``SEARCH_RESULTS_PER_PAGE``  | ``20``                    | Results per page                         |
+------------------------------+---------------------------+------------------------------------------+
| ``SEARCH_REQUEST_TIMEOUT``   | ``30``                    | HTTP timeout, in seconds                 |
+------------------------------+---------------------------+------------------------------------------+

The Docker Compose files already define a ``kalanjiyam-search`` service and
point the web and Celery containers at it. The image is built from
``build/containers/Dockerfile.opensearch``, which adds the **analysis-icu**
plugin — that plugin is required, not optional (see `Analysis`_).

The cluster is deliberately **not published to the host**. It runs with the
security plugin disabled and is reachable only from the Compose network. Do not
add a ``ports:`` mapping to it.


Getting started
---------------

.. code-block:: bash

   # 1. Turn it on
   echo "SEARCH_ENABLED=true" >> .env

   # 2. Start the stack (builds the OpenSearch image on first run)
   ./deploy/local/deploy.sh

   # 3. Create the indices and fill them
   docker exec -it kalanjiyam-web-dev python cli.py search-index init
   docker exec -it kalanjiyam-web-dev python cli.py search-index rebuild

   # 4. Check the result
   docker exec -it kalanjiyam-web-dev python cli.py search-index status


Index layout
------------

Indices are partitioned **per organization**. Each organization gets its own
physical index, addressed through an alias::

    kalanjiyam-pages-org-3          (alias, what searches target)
      -> kalanjiyam-store-pages-org-3-v7   (concrete index, what holds the data)

    kalanjiyam-projects-org-3
      -> kalanjiyam-store-projects-org-3-v7

The two namespaces never overlap. Searches expand the wildcard
``kalanjiyam-pages-org-*``, which matches aliases only — so a rebuild can
construct ``-v8`` alongside the live ``-v7`` without any of its half-written
documents becoming visible. The alias is swapped onto the new index in a single
atomic operation once the build succeeds, and the old index is then deleted. A
rebuild that fails leaves the live index untouched.

**Books that belong to no organization are not indexed.** They are a data
defect rather than a supported state; the dashboard and ``search-index status``
report how many exist. Attach them to a group and reindex.

Who can see what
~~~~~~~~~~~~~~~~

Every document also stores ``group_ids``, ``is_public``, and ``creator_id``, and
queries filter on them in addition to targeting the right indices — so an
index-selection bug alone cannot leak another tenant's manuscripts.

* **Anonymous visitors** see documents with ``is_public`` true, from any
  organization. This is the home-page search path.
* **Signed-in users** see their own organization's documents unfiltered, plus
  public documents from any organization.
* **Platform super admins** see everything.

All of this lives in one function, ``kalanjiyam.search.acl.search_scope``.

.. note::

   ``open-tenant`` — the default group for self-registered users — is treated
   as an ordinary organization here. Its members can therefore find each
   other's non-public books through search, which is broader than
   ``user_can_access_project`` allows elsewhere. ``creator_id`` is indexed so a
   per-creator filter can be switched on later without a reindex.


Analysis
--------

The corpus spans English plus roughly 22 mostly Indic languages. OpenSearch
ships no Tamil, Telugu, Malayalam, or Kannada analyzer, so per-language
analyzers are not an option for the main text field. Instead there is one
script-agnostic chain:

.. code-block:: text

   char_filter: icu_normalizer (nfkc_cf)
   tokenizer:   icu_tokenizer            # word boundaries in every script
   filter:      indic_normalization, lowercase, icu_folding

``icu_tokenizer`` and ``icu_folding`` come from the **analysis-icu** plugin;
without it, index creation fails outright. ``indic_normalization`` folds the
Indic spelling variants that OCR output is full of.

Two supporting fields:

* ``content.trigram`` — 3-grams, matched at a low boost so OCR noise and
  spelling variants still surface without drowning exact matches.
* ``content_en`` — the built-in ``english`` analyzer, populated only for pages
  detected as English, the one language where stemming clearly pays off.


Keeping the index current
-------------------------

Page edits index themselves. ``add_revision`` — the single choke point for
proofing saves — enqueues the page on the ``search_index`` Celery queue. Bulk
paths (JSONL import, project creation, DOCX import, admin ZIP import) enqueue
one task per finished book rather than one per page. Changing a project's
metadata, visibility, or group membership reindexes the whole project, because
those fields are stamped onto every one of its page documents and a group
change moves the book to a different index.

Every one of these hooks is **best-effort**. A dead broker or an unreachable
cluster is logged and ignored; it can never turn a successful page save into an
error.

``sync`` is the safety net. It compares the indexed revision id of every page
against the database, then upserts what drifted and deletes what no longer
exists. Run it after any bulk operation you are unsure about — it is far
cheaper than a rebuild.


CLI reference
-------------

All commands take ``--env`` (default ``development``).

.. code-block:: bash

   # Create indices and aliases for every organization
   python cli.py search-index init

   # Rebuild everything, one organization, or one book
   python cli.py search-index rebuild
   python cli.py search-index rebuild --org udaan
   python cli.py search-index rebuild --project some-book-slug

   # Reconcile without rebuilding
   python cli.py search-index sync
   python cli.py search-index sync --org udaan

   # Cluster health, per-org document counts, recent jobs
   python cli.py search-index status
   python cli.py search-index status --job-id 12

   # Delete one organization's indices (rebuildable from the database)
   python cli.py search-index drop --org udaan

``rebuild`` and ``sync`` queue a Celery task by default. Pass ``--now`` to run
in the calling process instead, which is useful in a container without a worker
or when you want to watch it fail.


Admin dashboards
----------------

+---------------------------------+-----------------------------------------------+
| URL                             | Who                                           |
+=================================+===============================================+
| ``/admin/platform/search_index``| Super admins: every organization              |
+---------------------------------+-----------------------------------------------+
| ``/admin/org/search_index``     | Org admins: their own organization only       |
+---------------------------------+-----------------------------------------------+

Both show cluster health, per-organization document counts and index size, a
job history, and a live progress bar while a job runs. Actions are rebuild,
sync, reindex-one-book, create-missing-indices, and drop.

The org id and project id in these forms are always checked against the
caller's authorized scope; for org admins the scope comes from
``require_org_admin()``, never from the request body. Dropping an index
requires typing the organization's slug. A second job cannot start for a scope
that already has one running.


Troubleshooting
---------------

**"Search is temporarily unavailable" on /search**
  The cluster is unreachable. Check ``docker logs kalanjiyam-search-prod`` and
  ``python cli.py search-index status``. The catalog and reader are unaffected.

**Index creation fails with "Unknown tokenizer type [icu_tokenizer]"**
  The analysis-icu plugin is missing — the container was built from the stock
  image rather than ``build/containers/Dockerfile.opensearch``. Rebuild it.

**OpenSearch exits immediately at startup**
  Almost always ``max virtual memory areas vm.max_map_count [65530] is too
  low``. On the host: ``sudo sysctl -w vm.max_map_count=262144`` (add it to
  ``/etc/sysctl.conf`` to persist).

**Results are missing recently edited pages**
  The Celery worker is not draining ``search_index``. Confirm the queue is in
  the worker's ``-Q`` list, then run ``python cli.py search-index sync``.

**Searches return nothing at all**
  Check that books belong to an organization —
  ``python cli.py search-index status`` reports how many do not. Groupless
  books are never indexed.
