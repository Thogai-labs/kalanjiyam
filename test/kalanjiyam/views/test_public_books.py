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
        endpoint = url_for("public.books.page", project_slug="test-project", page_slug="1")

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
        assert "When checked, anyone (including guests) can read this book at /books/." not in resp_edit.text
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
    from config import BaseConfig

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
    from kalanjiyam.admin import ProjectView
    import kalanjiyam.database as db
    import kalanjiyam.queries as q

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



