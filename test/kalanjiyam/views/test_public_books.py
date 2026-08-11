import kalanjiyam.database as db
import kalanjiyam.queries as q


def test_public_books_index(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.test_request_context("/kalanjiyam/books/"):
        from flask import url_for
        endpoint = url_for("public.books.index")

    resp = client.get(endpoint)
    assert resp.status_code == 200
    assert b"Test Project" in resp.data


def test_public_book_detail(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.test_request_context("/kalanjiyam/books/test-project/"):
        from flask import url_for
        endpoint = url_for("public.books.book", project_slug="test-project")

    resp = client.get(endpoint)
    assert resp.status_code == 200
    assert b"Test Project" in resp.data


def test_public_book_page(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.test_request_context("/kalanjiyam/books/test-project/1/"):
        from flask import url_for
        endpoint = url_for("public.books.page", project_slug="test-project", page_slug="1")

    resp = client.get(endpoint)
    assert resp.status_code == 200
    assert b"Foo" in resp.data
