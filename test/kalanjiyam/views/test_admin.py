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

        # Check inline table features: search input, separate scroll container, pagination
        assert b'x-model="search"' in resp.data
        assert b'max-h-[460px]' in resp.data
        assert b'x-ref="tableContainer"' in resp.data
        assert b'id="org-books-data"' in resp.data
        assert b'booksCatalogTable' in resp.data



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


def test_org_admin_members_select_only_shows_orphan_and_registered_users(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        open_tenant = q.get_or_create_open_tenant()

        org1 = db.Group(name="Org Users Alpha", slug="org-users-alpha")
        org2 = db.Group(name="Org Users Beta", slug="org-users-beta")
        session.add_all([org1, org2])
        session.flush()

        admin_user = db.User(username="users_alpha_admin", email="uadmin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org1.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org1.id))

        orphan_user = db.User(username="orphan_bob", email="bob@orphan.local")
        orphan_user.set_password("pass123")

        registered_user = db.User(username="registered_alice", email="alice@opentenant.local")
        registered_user.set_password("pass123")
        registered_user.organization_id = open_tenant.id

        user_in_org2 = db.User(username="beta_charlie", email="charlie@beta.local")
        user_in_org2.set_password("pass123")
        user_in_org2.organization_id = org2.id

        banned_user = db.User(username="banned_dave", email="dave@banned.local", is_banned=True)
        banned_user.set_password("pass123")

        deleted_user = db.User(username="deleted_eve", email="eve@deleted.local", is_deleted=True)
        deleted_user.set_password("pass123")

        session.add_all([orphan_user, registered_user, user_in_org2, banned_user, deleted_user])
        session.flush()

        session.add(db.UserGroups(user_id=registered_user.id, group_id=open_tenant.id))
        session.add(db.UserGroups(user_id=user_in_org2.id, group_id=org2.id))
        session.commit()

        client = flask_app.test_client(user=admin_user)
        resp = client.get("/admin/org/")
        assert resp.status_code == 200

        # Orphan user and registered user should appear in select options
        assert f'<option value="{orphan_user.id}">{orphan_user.username}'.encode() in resp.data
        assert f'<option value="{registered_user.id}">{registered_user.username}'.encode() in resp.data

        # User belonging to org2 should NOT appear
        assert f'<option value="{user_in_org2.id}">{user_in_org2.username}'.encode() not in resp.data

        # Current org admin (already member of org1) should NOT appear
        assert f'<option value="{admin_user.id}">{admin_user.username}'.encode() not in resp.data

        # Banned and deleted users should NOT appear
        assert f'<option value="{banned_user.id}">{banned_user.username}'.encode() not in resp.data
        assert f'<option value="{deleted_user.id}">{deleted_user.username}'.encode() not in resp.data


def test_org_admin_cannot_add_user_belonging_to_another_org(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        open_tenant = q.get_or_create_open_tenant()

        org1 = db.Group(name="Org Mutate Alpha", slug="org-mutate-alpha")
        org2 = db.Group(name="Org Mutate Beta", slug="org-mutate-beta")
        session.add_all([org1, org2])
        session.flush()

        admin_user = db.User(username="mutate_alpha_admin", email="madmin@test.local")
        admin_user.set_password("pass123")
        admin_user.organization_id = org1.id
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        admin_user.roles.append(org_role)
        session.add(admin_user)
        session.flush()
        session.add(db.UserGroups(user_id=admin_user.id, group_id=org1.id))

        orphan_user = db.User(username="mutate_orphan", email="morphan@test.local")
        orphan_user.set_password("pass123")

        registered_user = db.User(username="mutate_reg", email="mreg@test.local")
        registered_user.set_password("pass123")
        registered_user.organization_id = open_tenant.id

        user_in_org2 = db.User(username="mutate_beta_user", email="mbeta@test.local")
        user_in_org2.set_password("pass123")
        user_in_org2.organization_id = org2.id

        session.add_all([orphan_user, registered_user, user_in_org2])
        session.flush()

        session.add(db.UserGroups(user_id=registered_user.id, group_id=open_tenant.id))
        session.add(db.UserGroups(user_id=user_in_org2.id, group_id=org2.id))
        session.commit()

        client = flask_app.test_client(user=admin_user)

        # Attempt to add user_in_org2 to org1 should fail
        resp = client.post(
            "/admin/org/",
            data={"action": "add_user", "user_id": str(user_in_org2.id)},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Cannot add a user who already belongs to an organization" in resp.data
        members_org1 = [u.id for u in q.users_in_group(org1.id)]
        assert user_in_org2.id not in members_org1

        # Adding orphan_user to org1 should succeed
        resp2 = client.post(
            "/admin/org/",
            data={"action": "add_user", "user_id": str(orphan_user.id)},
            follow_redirects=True,
        )
        assert resp2.status_code == 200
        assert b"User added to organization" in resp2.data
        members_org1 = [u.id for u in q.users_in_group(org1.id)]
        assert orphan_user.id in members_org1

        # Adding registered_user (open-tenant) to org1 should succeed and clear open-tenant membership
        resp3 = client.post(
            "/admin/org/",
            data={"action": "add_user", "user_id": str(registered_user.id)},
            follow_redirects=True,
        )
        assert resp3.status_code == 200
        assert b"User added to organization" in resp3.data
        members_org1 = [u.id for u in q.users_in_group(org1.id)]
        assert registered_user.id in members_org1
        members_open_tenant = [u.id for u in q.users_in_group(open_tenant.id)]
        assert registered_user.id not in members_open_tenant


def test_admin_project_list__unauth(client):
    resp = client.get("/admin/project/")
    assert resp.status_code == 404


def test_admin_project_list__auth_admin(admin_client):
    resp = admin_client.get("/admin/project/")
    assert resp.status_code == 200
    assert "Slug" in resp.text
    assert "Creator" in resp.text
    assert "Org Name" in resp.text
    assert "Creation Mode" in resp.text
    assert "Total Pages" in resp.text
    assert "Total Storage Size" in resp.text
    assert "test-project" in resp.text
    # Edit pencil icon (&#x270E;) and edit action links must not appear in project list
    assert "&#x270E;" not in resp.text
    assert "/admin/project/edit/" not in resp.text

    # Directly visiting edit URL should redirect away
    edit_resp = admin_client.get("/admin/project/edit/?id=1")
    assert edit_resp.status_code == 302
    assert "/admin/project/" in edit_resp.headers.get("Location", "")

    # Check search and filter UI elements moved from proofing
    assert 'name="search"' in resp.text
    assert 'id="org-select"' in resp.text
    assert 'id="mode-select"' in resp.text
    assert "All Orgs" in resp.text
    assert "All Modes" in resp.text
    assert "Unregistered" in resp.text
    assert "Registered" in resp.text
    assert "Enterprise" in resp.text


def test_admin_project_list_filtering(admin_client, flask_app):
    # Filter by mode=unregistered (should return 0 since test-project has creator u-admin)
    resp_unreg = admin_client.get("/admin/project/?mode=unregistered")
    assert resp_unreg.status_code == 200
    assert "test-project" not in resp_unreg.text

    # Filter by mode=registered (test-project is not assigned to enterprise orgs)
    resp_reg = admin_client.get("/admin/project/?mode=registered")
    assert resp_reg.status_code == 200
    assert "test-project" in resp_reg.text

    # Search filter
    resp_search = admin_client.get("/admin/project/?search=test-project")
    assert resp_search.status_code == 200
    assert "test-project" in resp_search.text

    resp_search_none = admin_client.get("/admin/project/?search=nonexistent_project_xyz")
    assert resp_search_none.status_code == 200
    assert "test-project" not in resp_search_none.text


def test_proofing_superadmin_clean_up(superadmin_client):
    # On proofing dashboard, mode filter should be cleaned up
    resp = superadmin_client.get("/proofing/")
    assert resp.status_code == 200
    assert 'id="mode-select"' not in resp.text
    assert 'name="mode"' not in resp.text




def test_header_proofing_nav_hidden_for_superadmin(superadmin_client, moderator_client, client):
    # For superadmin, proofing dropdown is hidden in the header
    resp_super = superadmin_client.get("/")
    assert resp_super.status_code == 200
    assert "Proofing Navigation" not in resp_super.text
    assert "mobileProofingOpen = !mobileProofingOpen" not in resp_super.text

    # For moderator, proofing navigation dropdown is visible
    resp_mod = moderator_client.get("/")
    assert resp_mod.status_code == 200
    assert "Proofing Navigation" in resp_mod.text
    assert "mobileProofingOpen = !mobileProofingOpen" in resp_mod.text

    # For anonymous user, proofing navigation dropdown is visible
    resp_anon = client.get("/")
    assert resp_anon.status_code == 200
    assert "Proofing Navigation" in resp_anon.text
    assert "mobileProofingOpen = !mobileProofingOpen" in resp_anon.text






