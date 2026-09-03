import kalanjiyam.database as db
import kalanjiyam.queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils.org_access import user_can_access_project
from kalanjiyam.utils.user_mixins import KalanjiyamAnonymousUser


def _make_org(session, slug: str) -> db.Group:
    org = db.Group(name=slug.title(), slug=slug)
    session.add(org)
    session.flush()
    return org


def _make_user(session, username: str, org: db.Group | None, roles: list[str]) -> db.User:
    user = db.User(username=username, email=f"{username}@test.local")
    user.set_password("test-password")
    if org is not None:
        user.organization_id = org.id
    session.add(user)
    session.flush()
    if org is not None:
        session.add(db.UserGroups(user_id=user.id, group_id=org.id))
    for role_name in roles:
        role = session.query(db.Role).filter_by(name=role_name).one()
        user.roles.append(role)
    session.add(user)
    session.flush()
    return user


def _make_project(session, slug: str, org: db.Group | None, creator_id: int | None = None, fingerprint_id: str | None = None) -> db.Project:
    board = db.Board(title=f"{slug} discussion board")
    session.add(board)
    session.flush()
    project = db.Project(slug=slug, display_title=slug.title(), creator_id=creator_id, board_id=board.id, fingerprint_id=fingerprint_id)
    session.add(project)
    session.flush()
    if org is not None:
        session.add(db.ProjectGroups(group_id=org.id, project_id=project.id))
        session.flush()
    return project


def test_org_admin_requires_organization_id(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        org = _make_org(session, "alpha")
        user = _make_user(session, "orgadmin", org=None, roles=[SiteRole.ORG_ADMIN.value])
        assert user.is_org_admin is False

        user.organization_id = org.id
        assert user.is_org_admin is True
        session.commit()


def test_user_can_access_project_scoped_by_org(flask_app):
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org_a = _make_org(session, "org-a")
        org_b = _make_org(session, "org-b")
        member = _make_user(session, "member", org_a, roles=[SiteRole.P1.value])
        other = _make_user(session, "other", org_b, roles=[SiteRole.P1.value])
        project = _make_project(session, "book-a", org_a, creator_id=member.id)
        session.commit()

        assert user_can_access_project(member, project) is True
        assert user_can_access_project(other, project) is False
        assert user_can_access_project(KalanjiyamAnonymousUser(), project) is False

        project.is_publicly_viewable = True
        session.add(project)
        session.commit()
        assert user_can_access_project(other, project) is True
        assert user_can_access_project(KalanjiyamAnonymousUser(), project) is True


def test_user_can_view_proofing_project(flask_app):
    from kalanjiyam.utils.org_access import user_can_view_proofing_project
    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org_a = _make_org(session, "org-a-view")
        org_b = _make_org(session, "org-b-view")
        
        creator = _make_user(session, "creator-view", org_a, roles=[SiteRole.P1.value])
        member = _make_user(session, "member-view", org_a, roles=[SiteRole.P1.value])
        other = _make_user(session, "other-view", org_b, roles=[SiteRole.P1.value])
        super_admin = _make_user(session, "superadmin-view", org_b, roles=[SiteRole.SUPER_ADMIN.value])
        
        project = _make_project(session, "book-view", org_a, creator_id=creator.id)
        project.is_publicly_viewable = True
        session.add(project)
        session.commit()

        # Under user_can_view_proofing_project:
        # 1. Super admin can see
        assert user_can_view_proofing_project(super_admin, project) is True
        # 2. Creator can see
        assert user_can_view_proofing_project(creator, project) is True
        # 3. Same organization user (member) can see
        assert user_can_view_proofing_project(member, project) is True
        # 4. Other user cannot see in proofing (even though project is public)
        assert user_can_view_proofing_project(other, project) is False
        # 5. Anonymous user cannot see
        assert user_can_view_proofing_project(KalanjiyamAnonymousUser(), project) is False


def test_unregistered_guest_project_access_only_when_not_signed_in(flask_app):
    from kalanjiyam.utils.org_access import user_can_access_project, user_can_view_proofing_project

    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        open_tenant = q.get_or_create_open_tenant()
        org = _make_org(session, "test-guest-org")
        signed_in_user = _make_user(session, "signed_in_user", org, roles=[SiteRole.P1.value])

        guest_project = _make_project(
            session,
            slug="guest-book",
            org=open_tenant,
            creator_id=None,
            fingerprint_id="device-fp-123",
        )
        session.commit()

        anonymous_user = KalanjiyamAnonymousUser()

        # Test with request context containing device fingerprint cookie
        with flask_app.test_request_context(headers={"Cookie": "device_fingerprint=device-fp-123"}):
            # 1. Unregistered (not signed in) user WITH matching fingerprint CAN view/access
            assert user_can_access_project(anonymous_user, guest_project) is True
            assert user_can_view_proofing_project(anonymous_user, guest_project) is True

            # 2. Signed-in user WITH matching fingerprint CANNOT view/access guest project
            assert user_can_access_project(signed_in_user, guest_project) is False
            assert user_can_view_proofing_project(signed_in_user, guest_project) is False


def test_master_user_multi_org_access(flask_app):
    from kalanjiyam.utils.org_access import user_can_access_project, user_can_view_proofing_project

    flask_app.config["MULTI_TENANT_MODE"] = True
    flask_app.config["ENFORCE_ORG_ACCESS"] = True

    with flask_app.app_context():
        session = q.get_session()
        org_a = _make_org(session, "master-org-a")
        org_b = _make_org(session, "master-org-b")
        org_c = _make_org(session, "master-org-c")

        # Create master user assigned to Org A and Org B
        master_user = _make_user(session, "master_user_1", org_a, roles=[SiteRole.MASTER_USER.value])
        session.add(db.UserGroups(user_id=master_user.id, group_id=org_b.id))
        session.flush()

        proj_a = _make_project(session, "proj-in-a", org_a)
        proj_b = _make_project(session, "proj-in-b", org_b)
        proj_c = _make_project(session, "proj-in-c", org_c)
        session.commit()

        # Master user can access projects from both assigned organizations
        assert user_can_access_project(master_user, proj_a) is True
        assert user_can_access_project(master_user, proj_b) is True
        assert user_can_view_proofing_project(master_user, proj_a) is True
        assert user_can_view_proofing_project(master_user, proj_b) is True

        # Master user cannot access project from unassigned organization C
        assert user_can_access_project(master_user, proj_c) is False
        assert user_can_view_proofing_project(master_user, proj_c) is False
        assert master_user.is_master_user is True


