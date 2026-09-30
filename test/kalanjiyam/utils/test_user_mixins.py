from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.enums import SiteRole
from kalanjiyam.utils.user_mixins import KalanjiyamAnonymousUser


def test_anonymous_user():
    u = KalanjiyamAnonymousUser()
    assert not u.is_p1
    assert not u.is_p2
    assert not u.is_proofreader
    assert not u.is_moderator
    assert not u.is_admin
    assert not u.has_role(SiteRole.P1)
    assert u.is_guest
    assert not u.is_registered_user
    assert not u.is_meta_analyst
    assert u.has_role(SiteRole.GUEST)
    assert u.can_create_project

    assert u.is_ok


def test_new_authenticated_user(client):
    u = db.User()
    session = q.get_session()
    p1 = session.query(db.Role).filter_by(name=SiteRole.P1.value).one()

    u.roles = [p1]
    assert u.is_p1
    assert not u.is_p2
    assert u.is_proofreader
    assert not u.is_moderator
    assert not u.is_admin
    assert not u.is_registered_user
    assert not u.is_guest
    assert not u.can_create_project


def test_registered_user(client):
    u = db.User()
    session = q.get_session()
    reg_role = session.query(db.Role).filter_by(name=SiteRole.REGISTERED_USER.value).one()

    u.roles = [reg_role]
    assert u.is_registered_user
    assert not u.is_guest
    assert u.is_p1
    assert u.is_proofreader
    assert not u.is_moderator
    assert not u.is_admin
    assert u.can_create_project


def test_meta_analyst_user(client):
    u = db.User()
    session = q.get_session()
    role = session.query(db.Role).filter_by(name=SiteRole.META_ANALYST.value).one()

    u.roles = [role]
    assert u.is_meta_analyst
    assert not u.is_p1
    assert not u.is_p2
    assert not u.is_proofreader
    assert not u.is_moderator
    assert not u.is_admin
    assert not u.is_registered_user
    assert not u.is_guest
    assert not u.can_create_project

