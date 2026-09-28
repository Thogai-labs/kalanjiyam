import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole


def test_admin_index__unauth(client):
    resp = client.get("/admin/")
    assert resp.status_code == 404


def test_admin_index__auth_admin(admin_client):
    resp = admin_client.get("/admin/")
    assert resp.status_code == 302


def test_admin_index__auth_moderator(moderator_client):
    resp = moderator_client.get("/admin/")
    assert resp.status_code == 200


def test_admin_index__inactive(deleted_client, banned_client):
    assert deleted_client.get("/admin/").status_code == 404
    assert banned_client.get("/admin/").status_code == 404


def test_admin_text__unauth(client):
    resp = client.get("/admin/text/")
    assert resp.status_code == 404


def test_admin_text__admin_or_moderator(admin_client, moderator_client):
    resp = admin_client.get("/admin/projectsponsorship/")
    assert resp.status_code == 200

    resp = moderator_client.get("/admin/projectsponsorship/")
    assert resp.status_code == 200


def test_admin_text__admin_only(admin_client, moderator_client):
    resp = admin_client.get("/admin/text/")
    assert resp.status_code == 200

    resp = moderator_client.get("/admin/text/")
    assert resp.status_code == 404


def test_admin_text__inactive(deleted_client, banned_client):
    assert deleted_client.get("/admin/text/").status_code == 404
    assert banned_client.get("/admin/text/").status_code == 404


def test_org_admin_create_user_unauth(client):
    resp = client.get("/admin/org/user/create")
    assert resp.status_code == 404


def test_org_admin_create_user_flow(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = db.Group(name="Org Test Flow", slug="org-create-user-flow-test")
        session.add(org)
        session.flush()

        admin_user = db.User(username="test_org_admin_flow", email="orgadminflow@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org.id))
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # GET separate UI page
        resp = client.get("/admin/org/user/create")
        assert resp.status_code == 200
        assert b"Create Organization User" in resp.data
        assert b"/admin/org/user/create" in resp.data

        # POST creates user
        post_resp = client.post(
            "/admin/org/user/create",
            data={
                "username": "new_proofer_user",
                "email": "new_proofer@test.local",
                "password": "secure_password_123",
                "role_name": "p2",
            },
            follow_redirects=True,
        )
        assert post_resp.status_code == 200

        # Check user in DB
        user = session.query(db.User).filter_by(username="new_proofer_user").first()
        assert user is not None
        assert user.email == "new_proofer@test.local"
        assert any(r.name == "p2" for r in user.roles)
        assert any(g.id == org.id for g in user.groups)


def test_org_admin_books_dropdown_only_shows_orphan_books(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        board = db.Board()
        session.add(board)
        session.flush()

        org1 = db.Group(name="Org One", slug="org-one-test")
        org2 = db.Group(name="Org Two", slug="org-two-test")
        session.add_all([org1, org2])
        session.flush()

        admin_user = db.User(username="org1_admin", email="org1admin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org1.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org1.id))

        book_in_org1 = db.Project(slug="book-in-org1", display_title="Book Belonging To Org 1", board_id=board.id)
        book_in_org2 = db.Project(slug="book-in-org2", display_title="Book Belonging To Org 2", board_id=board.id)
        orphan_book = db.Project(slug="orphan-book", display_title="Orphan Book Free", board_id=board.id)
        session.add_all([book_in_org1, book_in_org2, orphan_book])
        session.flush()

        session.add(db.ProjectGroups(group_id=org1.id, project_id=book_in_org1.id))
        session.add(db.ProjectGroups(group_id=org2.id, project_id=book_in_org2.id))
        session.commit()

        # Query tests
        assert q.project_belongs_to_any_group(book_in_org1.id) is True
        assert q.project_belongs_to_any_group(book_in_org2.id) is True
        assert q.project_belongs_to_any_group(orphan_book.id) is False

        orphans = q.orphan_projects_for_group_select()
        orphan_ids = {p.id for p in orphans}
        assert orphan_book.id in orphan_ids
        assert book_in_org1.id not in orphan_ids
        assert book_in_org2.id not in orphan_ids

        # Client GET dashboard test
        client = flask_app.test_client(user=admin_user)
        resp = client.get("/admin/org/")
        assert resp.status_code == 200

        # Select dropdown should contain orphan book
        option_orphan = f'<option value="{orphan_book.id}">{orphan_book.slug} — {orphan_book.display_title}'.encode()
        assert option_orphan in resp.data

        # Select dropdown should NOT contain book from org 2
        option_org2 = f'<option value="{book_in_org2.id}">{book_in_org2.slug}'.encode()
        assert option_org2 not in resp.data

        # Select dropdown should NOT contain book from org 1
        option_org1 = f'<option value="{book_in_org1.id}">{book_in_org1.slug}'.encode()
        assert option_org1 not in resp.data


def test_org_admin_cannot_add_non_orphan_book(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        board = db.Board()
        session.add(board)
        session.flush()

        org1 = db.Group(name="Org Alpha", slug="org-alpha-test")
        org2 = db.Group(name="Org Beta", slug="org-beta-test")
        session.add_all([org1, org2])
        session.flush()

        admin_user = db.User(username="alpha_admin", email="alphaadmin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org1.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org1.id))

        book_in_org2 = db.Project(slug="beta-book", display_title="Beta Org Book", board_id=board.id)
        orphan_book = db.Project(slug="orphan-alpha", display_title="Orphan For Alpha", board_id=board.id)
        session.add_all([book_in_org2, orphan_book])
        session.flush()

        session.add(db.ProjectGroups(group_id=org2.id, project_id=book_in_org2.id))
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # Attempt to add book_in_org2 to org1 should fail
        resp = client.post(
            "/admin/org/",
            data={"action": "add_project", "project_id": str(book_in_org2.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Cannot add a book that already belongs to an organization" in resp.data
        assert not q.project_belongs_to_group(book_in_org2.id, org1.id)

        # Adding orphan_book to org1 should succeed
        resp2 = client.post(
            "/admin/org/",
            data={"action": "add_project", "project_id": str(orphan_book.id)},
            follow_redirects=True,
        )
        assert resp2.status_code == 200
        assert b"Book added to organization" in resp2.data
        assert q.project_belongs_to_group(orphan_book.id, org1.id)


