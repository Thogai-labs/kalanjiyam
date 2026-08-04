"""Contract tests against the installed opensearch-py.

The client validates query parameters and raises TypeError for unknown ones.
Most of our calls sit inside broad ``except Exception`` handlers, so a
rejected parameter does not crash -- it silently degrades. That is how
``indices.stats(ignore_unavailable=True)`` came to report every index as
0 bytes.

These tests point a client at a closed port: a ConnectionError means the
arguments were accepted and a request was actually attempted, while a
TypeError means the library rejected them. No server required.
"""

import pytest

opensearchpy = pytest.importorskip("opensearchpy")

from opensearchpy import OpenSearch  # noqa: E402
from opensearchpy.exceptions import ConnectionError as OSConnectionError  # noqa: E402

#: Nothing listens here, so connections are refused immediately.
CLOSED_PORT = "http://127.0.0.1:59199"

MATCH_ALL = {"query": {"match_all": {}}}


@pytest.fixture()
def client():
    return OpenSearch(hosts=[CLOSED_PORT], timeout=1, max_retries=0)


def assert_accepted(call):
    """The call reached the transport, i.e. its arguments were valid."""
    try:
        call()
    except OSConnectionError:
        return
    except TypeError as e:
        pytest.fail(f"opensearch-py rejected an argument we pass: {e}")
    except Exception:
        # Any other error still means it got past argument validation.
        return


def test_search_accepts_our_query_params(client):
    assert_accepted(
        lambda: client.search(
            index="x-*",
            body=MATCH_ALL,
            ignore_unavailable=True,
            allow_no_indices=True,
        )
    )


def test_count_accepts_ignore_unavailable(client):
    assert_accepted(lambda: client.count(index="x", ignore_unavailable=True))


def test_index_accepts_id_and_body(client):
    assert_accepted(lambda: client.index(index="x", id="page:1", body={"a": 1}))


def test_delete_by_query_accepts_conflicts_and_refresh(client):
    assert_accepted(
        lambda: client.delete_by_query(
            index="x", body=MATCH_ALL, conflicts="proceed", refresh=True
        )
    )
    assert_accepted(
        lambda: client.delete_by_query(
            index="x-*",
            body=MATCH_ALL,
            conflicts="proceed",
            ignore_unavailable=True,
            refresh=True,
        )
    )


def test_indices_lifecycle_calls_accept_our_params(client):
    assert_accepted(lambda: client.indices.create(index="x", body={"settings": {}}))
    assert_accepted(lambda: client.indices.delete(index="x", ignore_unavailable=True))
    assert_accepted(lambda: client.indices.get(index="x-*", ignore_unavailable=True))
    assert_accepted(lambda: client.indices.get_alias(name="x-*", ignore_unavailable=True))
    assert_accepted(lambda: client.indices.update_aliases(body={"actions": []}))
    assert_accepted(lambda: client.indices.refresh(index="a,b", ignore_unavailable=True))


def test_indices_stats_does_not_accept_ignore_unavailable(client):
    """Pins the asymmetry that caused the silent 0-byte bug.

    If a future release starts accepting it, this test fails and we can
    simplify org_stats. Until then, org_stats must not pass it.
    """
    with pytest.raises(TypeError):
        client.indices.stats(index="x", ignore_unavailable=True)

    assert_accepted(lambda: client.indices.stats(index="x"))


def test_cluster_health_is_callable(client):
    assert_accepted(lambda: client.cluster.health())


def test_helpers_we_rely_on_exist():
    from opensearchpy.helpers import scan, streaming_bulk

    assert callable(streaming_bulk)
    assert callable(scan)
