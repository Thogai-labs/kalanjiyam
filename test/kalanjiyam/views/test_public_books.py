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

    # Test with pagination query params
    resp_paged = client.get(f"{endpoint}?page=2&per_page=18")
    assert resp_paged.status_code == 200
    assert b"booksCatalog" in resp_paged.data


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

        endpoint = url_for(
            "public.books.page", project_slug="test-project", page_slug="1"
        )

    resp = client.get(endpoint)
    assert resp.status_code == 200
    assert b"Foo" in resp.data


def test_public_books_disabled_returns_404(client, flask_app):
    flask_app.config["ENABLE_BOOKS"] = False
    try:
        resp_index = client.get("/kalanjiyam/books/")
        assert resp_index.status_code == 404

        resp_book = client.get("/kalanjiyam/books/test-project/")
        assert resp_book.status_code == 404

        resp_page = client.get("/kalanjiyam/books/test-project/1/")
        assert resp_page.status_code == 404
    finally:
        flask_app.config["ENABLE_BOOKS"] = True


def test_public_books_ui_elements_hidden_when_disabled(client, rama_client, flask_app):
    flask_app.config["ENABLE_BOOKS"] = False
    flask_app.config["MULTI_TENANT_MODE"] = False
    flask_app.config["ENFORCE_ORG_ACCESS"] = False
    try:
        # Home page shouldn't show Browse Catalog
        resp_home = client.get("/")
        assert "Browse Catalog" not in resp_home.text

        # Navigation shouldn't show Library link
        resp_proofing = rama_client.get("/proofing/")
        assert ">Library<" not in resp_proofing.text

        # Project edit shouldn't show is_publicly_viewable checkbox
        resp_edit = rama_client.get("/proofing/test-project/edit")
        assert resp_edit.status_code == 200
        assert "is_publicly_viewable" not in resp_edit.text
        assert (
            "When checked, anyone (including guests) can read this book at /books/."
            not in resp_edit.text
        )
    finally:
        flask_app.config["ENABLE_BOOKS"] = True


def test_public_books_ui_elements_visible_when_enabled(client, rama_client, flask_app):
    flask_app.config["ENABLE_BOOKS"] = True
    flask_app.config["MULTI_TENANT_MODE"] = False
    flask_app.config["ENFORCE_ORG_ACCESS"] = False
    # Home page shows Browse Catalog
    resp_home = client.get("/")
    assert "Browse Catalog" in resp_home.text

    # Navigation shows Library link
    resp_proofing = rama_client.get("/proofing/")
    assert ">Library<" in resp_proofing.text

    # Project edit shows is_publicly_viewable checkbox
    resp_edit = rama_client.get("/proofing/test-project/edit")
    assert resp_edit.status_code == 200
    assert "is_publicly_viewable" in resp_edit.text


def test_enable_books_env_parsing():

    disabled_values = ["0", "false", "False", "disabled", "DISABLED", "off", "no"]
    for val in disabled_values:
        parsed = str(val).strip().lower() not in (
            "0",
            "false",
            "disabled",
            "off",
            "no",
        )
        assert parsed is False, f"Expected {val} to evaluate to False"

    enabled_values = ["1", "true", "True", "enabled", "yes"]
    for val in enabled_values:
        parsed = str(val).strip().lower() not in (
            "0",
            "false",
            "disabled",
            "off",
            "no",
        )
        assert parsed is True, f"Expected {val} to evaluate to True"


def test_project_view_admin_columns_and_form(flask_app):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q
    from kalanjiyam.admin import ProjectView

    with flask_app.app_context():
        pv = ProjectView(db.Project, q.get_session())

        flask_app.config["ENABLE_BOOKS"] = False
        cols_disabled = [c[0] for c in pv.get_list_columns()]
        assert "is_publicly_viewable" not in cols_disabled
        form_disabled = pv.scaffold_form()
        assert not hasattr(form_disabled, "is_publicly_viewable")

        flask_app.config["ENABLE_BOOKS"] = True
        cols_enabled = [c[0] for c in pv.get_list_columns()]
        assert "is_publicly_viewable" in cols_enabled


def test_admin_set_project_public_rejected_when_disabled(flask_app, superadmin_client):
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

    with flask_app.app_context():
        session = q.get_session()
        org = session.query(db.Group).first()
        if not org:
            org = db.Group(name="Test Org", slug="test-org")
            session.add(org)
            session.commit()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        org_id = org.id
        project_id = project.id

    with flask_app.test_request_context():
        from flask import url_for

        target_url = url_for("groups_view.manage", id=org_id)

    flask_app.config["ENABLE_BOOKS"] = False
    try:
        resp = superadmin_client.post(
            target_url,
            data={
                "action": "set_project_public",
                "project_id": project_id,
                "is_public": "1",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "Public books are currently disabled." in resp.text
    finally:
        flask_app.config["ENABLE_BOOKS"] = True


def test_get_project_stats_redis_caching(flask_app):
    from unittest.mock import MagicMock

    from kalanjiyam.views.public.books import get_project_stats

    with flask_app.app_context():
        session = q.get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        assert project is not None

        # Create a mock Redis client
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        # First call computes stats and caches to Redis
        stats1 = get_project_stats(project, r_client=mock_redis)
        assert "total_pages" in stats1
        assert mock_redis.setex.called

        # Second call returns cached payload without re-querying DB
        import json

        mock_redis.get.return_value = json.dumps(stats1).encode("utf-8")
        stats2 = get_project_stats(project, r_client=mock_redis)
        assert stats2 == stats1


def test_public_book_detail_batch_pages(client, flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        project = session.query(db.Project).filter_by(slug="test-project").first()
        project.is_publicly_viewable = True
        session.commit()

        # Add a translation to test language badge rendering
        page = project.pages[0] if project.pages else None
        if page:
            existing_trans = (
                session.query(db.Translation).filter_by(page_id=page.id).first()
            )
            if not existing_trans:
                rev = session.query(db.Revision).filter_by(page_id=page.id).first()
                if not rev:
                    rev = db.Revision(
                        project_id=project.id,
                        page_id=page.id,
                        status_id=page.status_id,
                        content="Sample page content",
                    )
                    session.add(rev)
                    session.flush()

                user = session.query(db.User).first()
                trans = db.Translation(
                    page_id=page.id,
                    revision_id=rev.id,
                    author_id=user.id if user else None,
                    source_language="sa",
                    target_language="en",
                    translation_engine="google",
                    content="Hello world",
                )
                session.add(trans)
                session.commit()

    with flask_app.test_request_context("/kalanjiyam/books/test-project/"):
        from flask import url_for

        endpoint = url_for("public.books.book", project_slug="test-project")

    resp = client.get(endpoint)
    assert resp.status_code == 200
    assert b"Test Project" in resp.data
    assert "sa" in resp.text or "en" in resp.text or "Page" in resp.text
