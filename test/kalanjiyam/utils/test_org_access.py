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


def _make_project(session, slug: str, org: db.Group | None, creator_id: int) -> db.Project:
    project = db.Project(slug=slug, display_title=slug.title(), creator_id=creator_id)
    session.add(project)
    session.flush()
    if org is not None:
        session.add(db.ProjectGroups(group_id=org.id, project_id=project.id))
        session.flush()
    return project


def test_org_admin_requires_organization_id(app):
    with app.app_context():
        session = q.get_session()
        org = _make_org(session, "alpha")
        user = _make_user(session, "orgadmin", org=None, roles=[SiteRole.ORG_ADMIN.value])
        assert user.is_org_admin is False

        user.organization_id = org.id
        assert user.is_org_admin is True
        session.commit()


def test_user_can_access_project_scoped_by_org(app):
    app.config["MULTI_TENANT_MODE"] = True
    app.config["ENFORCE_ORG_ACCESS"] = True

    with app.app_context():
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
