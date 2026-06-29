import kalanjiyam.database as db
from kalanjiyam.queries import get_session


def _cleanup(session, *objects):
    for object in objects:
        session.delete(object)
    session.commit()


def test_text__str(client):
    t = db.Text(slug="test-slug", title="Test title")
    assert str(t) == "test-slug"


def test_user__is_ok_when_created(client):
    session = get_session()
    user = db.User(username="test", email="test@siddhasagaram.in")
    user.set_password("my-password")
    session.add(user)
    session.commit()

    assert user.is_ok

    _cleanup(session, user)


def test_user__set_and_check_password(client):
    session = get_session()
    user = db.User(username="test", email="test@siddhasagaram.in")
    user.set_password("my-password")
    session.add(user)
    session.commit()

    assert user.check_password("my-password")
    assert not user.check_password("my-password2")

    _cleanup(session, user)


def test_user__set_and_check_role(client):
    session = get_session()
    user = db.User(username="test", email="test@siddhasagaram.in")
    user.set_password("my-password")
    session.add(user)
    session.flush()

    p1 = session.query(db.Role).filter_by(name=db.SiteRole.P1.value).one()
    user.roles.append(p1)
    session.commit()

    assert user.is_proofreader
    assert not user.is_admin

    _cleanup(session, user)


def test_user__deletion(client):
    session = get_session()

    # Check active user
    user = db.User(username="test", email="test@siddhasagaram.in")
    user.set_password("my-password")
    session.add(user)
    session.commit()
    assert user.is_ok

    # Deleted
    user.set_is_deleted(True)
    session.add(user)
    session.commit()
    assert not user.is_ok

    _cleanup(session, user)


def test_role__repr(client):
    role = db.Role(name="foo")
    assert repr(role) == "<Role(None, 'foo')>"


def test_token__set_and_check_token(client):
    session = get_session()
    row = db.PasswordResetToken(user_id=1)
    row.set_token("password")
    session.add(row)
    session.commit()

    assert row.check_token("password")
    assert not row.check_token("password2")

    _cleanup(session, row)


def test_project__creator_mode(client):
    session = get_session()

    # 1. Unregistered project (has fingerprint_id)
    project_unregistered = db.Project(
        slug="unregistered-proj",
        display_title="Unregistered Project",
        fingerprint_id="some-fingerprint",
    )
    session.add(project_unregistered)

    # 2. Registered project (has creator_id, no custom enterprise group)
    project_registered = db.Project(
        slug="registered-proj",
        display_title="Registered Project",
        creator_id=1,
    )
    # Give it the open-tenant group
    open_tenant = session.query(db.Group).filter_by(slug="open-tenant").first()
    if not open_tenant:
        open_tenant = db.Group(name="Open Tenant", slug="open-tenant")
        session.add(open_tenant)
        session.flush()
    project_registered.groups.append(open_tenant)
    session.add(project_registered)

    # 3. Enterprise project (has creator_id, has enterprise group)
    project_enterprise = db.Project(
        slug="enterprise-proj",
        display_title="Enterprise Project",
        creator_id=1,
    )
    enterprise_group = db.Group(name="Enterprise Org", slug="enterprise-org")
    session.add(enterprise_group)
    session.flush()
    project_enterprise.groups.append(enterprise_group)
    session.add(project_enterprise)

    session.commit()

    try:
        assert project_unregistered.creator_mode == "unregistered"
        assert project_registered.creator_mode == "registered"
        assert project_enterprise.creator_mode == "enterprise"
    finally:
        _cleanup(session, project_unregistered, project_registered, project_enterprise, enterprise_group)

