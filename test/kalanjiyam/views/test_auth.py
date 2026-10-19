from datetime import datetime

from flask_login import current_user

from kalanjiyam import database as db
from kalanjiyam import queries as q
from kalanjiyam.views import auth


def _cleanup(session, *objects):
    for object in objects:
        session.delete(object)
    session.commit()


def test_is_valid_reset_token():
    row = db.PasswordResetToken(
        user_id=1,
        is_active=True,
        created_at=datetime(2022, 1, 15, 12, 0, 0),
    )
    inactive_row = db.PasswordResetToken(
        user_id=1,
        is_active=False,
        created_at=datetime(2022, 1, 15, 12, 0, 0),
    )
    row.set_token("secret")
    inactive_row.set_token("secret")

    # OK
    assert auth._is_valid_reset_token(
        row, "secret", now=datetime(2022, 1, 15, 13, 0, 0)
    )
    # Too old
    assert not auth._is_valid_reset_token(
        row, "secret", now=datetime(2022, 1, 18, 12, 0, 0)
    )
    # Bad token
    assert not auth._is_valid_reset_token(
        row, "secret2", now=datetime(2022, 1, 15, 13, 0, 0)
    )
    # Missing row
    assert not auth._is_valid_reset_token(
        None, "secret", now=datetime(2022, 1, 15, 13, 0, 0)
    )
    # Inactive
    assert not auth._is_valid_reset_token(
        inactive_row, "secret", now=datetime(2022, 1, 15, 13, 0, 0)
    )


def test_register__unauth(client):
    r = client.get("/register")
    assert r.status_code == 200
    assert "Create a Kalanjiyam account" in r.text


def test_register__unauth_post__ok(client):
    data = {
        "username": "krishna",
        "password": "narayana",
        "email": "krishna@mbh.org",
    }
    with client:
        r = client.post("/register", data=data)
        assert r.status_code == 302
        assert current_user.username == "krishna"
        assert current_user.is_ok


def test_register__auth(rama_client):
    r = rama_client.get("/register")
    assert r.status_code == 302


def test_register__banned(banned_client):
    with banned_client:
        r = banned_client.get("/register")
        assert r.status_code == 200
        assert current_user.is_anonymous


def test_sign_in__unauth(client):
    r = client.get("/sign-in")
    assert "Sign In" in r.text and "Welcome back to Kalanjiyam" in r.text


def test_sign_in__unauth_post__ok(client):
    r = client.post(
        "/sign-in",
        data={
            "username": "u-basic",
            "password": "pass_basic",
        },
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/proofing/")


def test_sign_in__superadmin_redirects_to_platform(client):
    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()

    r = client.post(
        "/sign-in",
        data={
            "username": "u-superadmin",
            "password": "pass_superadmin",
        },
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/platform/")


def test_sign_in__superadmin_auth_get_redirects_to_platform(superadmin_client):
    r = superadmin_client.get("/sign-in")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/platform/")


def test_sign_in__superadmin_next_url(client):
    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()

    # Safe admin next URL
    r_safe = client.post(
        "/sign-in?next=/admin/platform/settings",
        data={
            "username": "u-superadmin",
            "password": "pass_superadmin",
        },
    )
    assert r_safe.status_code == 302
    assert r_safe.headers["Location"].endswith("/admin/platform/settings")

    # If next is /proofing/, superadmin should still go to platform dashboard
    r_proofing = client.post(
        "/sign-in?next=/proofing/",
        data={
            "username": "u-superadmin",
            "password": "pass_superadmin",
        },
    )
    assert r_proofing.status_code == 302
    assert r_proofing.headers["Location"].endswith("/admin/platform/")

    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()


def test_sign_in__org_admin_redirects_to_dashboard(flask_app, client):
    with flask_app.app_context():
        session = q.get_session()
        org = session.query(db.Group).filter_by(slug="test-auth-org").first()
        if not org:
            org = db.Group(name="Test Auth Org", slug="test-auth-org")
            session.add(org)
            session.flush()

        from kalanjiyam.enums import SiteRole
        org_role = session.query(db.Role).filter_by(name=SiteRole.ORG_ADMIN.value).first()
        user = session.query(db.User).filter_by(username="u-orgadmin-authtest").first()
        if not user:
            user = db.User(
                username="u-orgadmin-authtest",
                email="orgadmin_authtest@test.local",
                organization_id=org.id,
            )
            user.set_password("pass_orgadmin")
            user.roles.append(org_role)
            session.add(user)
            session.flush()
            session.add(db.UserGroups(user_id=user.id, group_id=org.id))
            session.commit()

        session.query(db.UsageLog).filter_by(action="sign_in").delete()
        session.commit()

    r = client.post(
        "/sign-in",
        data={
            "username": "u-orgadmin-authtest",
            "password": "pass_orgadmin",
        },
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/org/")

    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()


def test_sign_in__org_admin_auth_get_redirects_to_dashboard(flask_app):
    with flask_app.app_context():
        session = q.get_session()
        user = session.query(db.User).filter_by(username="u-orgadmin-authtest").first()

    org_client = flask_app.test_client(user=user)
    r = org_client.get("/sign-in")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/admin/org/")


def test_sign_in__org_admin_next_url(flask_app, client):
    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()

    # Safe org admin next URL
    r_safe = client.post(
        "/sign-in?next=/admin/org/create-user",
        data={
            "username": "u-orgadmin-authtest",
            "password": "pass_orgadmin",
        },
    )
    assert r_safe.status_code == 302
    assert r_safe.headers["Location"].endswith("/admin/org/create-user")

    # If next is /proofing/, org admin should still go to org dashboard
    r_proofing = client.post(
        "/sign-in?next=/proofing/",
        data={
            "username": "u-orgadmin-authtest",
            "password": "pass_orgadmin",
        },
    )
    assert r_proofing.status_code == 302
    assert r_proofing.headers["Location"].endswith("/admin/org/")

    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()



def test_sign_in__unauth_post__bad_username(client):
    r = client.post(
        "/sign-in",
        data={
            "username": "u-attacker",
            "password": "pass_basic",
        },
    )
    assert "Invalid username or password" in r.text


def test_sign_in__unauth_post__bad_password(client):
    r = client.post(
        "/sign-in",
        data={
            "username": "u-basic",
            "password": "bad password",
        },
    )
    assert "Invalid username or password" in r.text


def test_sign_in__auth(rama_client):
    r = rama_client.get("/sign-in")
    assert r.status_code == 302


def test_sign_in__banned(banned_client):
    with banned_client:
        r = banned_client.get("/sign-in")
        assert r.status_code == 200
        assert current_user.is_anonymous


def test_sign_out__unauth(client):
    with client:
        r = client.get("/sign-out")
        assert r.status_code == 302
        assert current_user.is_anonymous


def test_sign_out__auth(rama_client):
    with rama_client:
        r = rama_client.get("/sign-out")
        assert r.status_code == 302
        assert current_user.is_anonymous


def test_sign_in__case_insensitive(client):
    r = client.post(
        "/sign-in",
        data={
            "username": "U-BASIC",
            "password": "pass_basic",
        },
    )
    assert r.status_code == 302


def test_reset_password__anti_enumeration(client):
    # Valid email
    r1 = client.post("/reset-password", data={"email": "admin@example.com"})
    assert r1.status_code == 200
    assert "Instructions sent to" in r1.text or "admin@example.com" in r1.text

    # Non-existent email should return same response template without error revealing account missing
    r2 = client.post("/reset-password", data={"email": "nonexistent@example.com"})
    assert r2.status_code == 200
    assert "Sorry, the email address you provided is not associated" not in r2.text
    assert "nonexistent@example.com" in r2.text


def test_get_reset_password_token__get(client):
    r = client.get("/reset-password")
    assert "Reset your password" in r.text


def test_reset_password_from_token(client):
    with client:
        user = q.user("u-admin")

    user_id = user.id
    raw_token = auth._create_reset_token(user_id)

    r = client.get("/reset-password/u-admin/bad_token")
    assert r.status_code == 302

    r = client.get(f"/reset-password/bad-user/{raw_token}")
    assert r.status_code == 302

    r = client.get(f"/reset-password/u-admin/{raw_token}")
    assert "Set New Password" in r.text


def test_change_password(rama_client):
    r = rama_client.get("/change-password")
    assert ">Change" in r.text


def test_change_password__ok(client):
    # Create a dummy user
    session = q.get_session()
    user = db.User(username="sample-user", email="foo@siddhasagaram.in")
    user.set_password("password")
    session.add(user)
    session.commit()

    # Sign in
    r = client.post(
        "/sign-in",
        data={
            "username": "sample-user",
            "password": "password",
        },
    )
    assert r.status_code == 302

    # Try the password change
    r = client.post(
        "/change-password",
        data={
            "old_password": "password",
            "new_password": "password2",
        },
    )
    # `302` indicates success.
    assert r.status_code == 302


def test_change_password__unauth(client):
    r = client.get("/change-password")
    assert r.status_code == 302


def test_change_password__bad_old_password(rama_client):
    r = rama_client.post(
        "/change-password",
        data={
            "old_password": "bad-password",
            "new_password": "new-password",
        },
    )
    assert "Old password isn&#39;t valid" in r.text


def test_sign_in__rate_limiting(client):
    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="sign_in").delete()
    session.commit()

    for i in range(5):
        r = client.post(
            "/sign-in",
            data={
                "username": "u-basic",
                "password": "wrong_password",
            },
        )
        assert "Too many login attempts" not in r.text

    # 6th attempt should be rate limited
    r = client.post(
        "/sign-in",
        data={
            "username": "u-basic",
            "password": "wrong_password",
        },
    )
    assert "Too many login attempts" in r.text


def test_register__rate_limiting(client):
    session = q.get_session()
    session.query(db.UsageLog).filter_by(action="register").delete()
    session.commit()

    for i in range(5):
        r = client.post(
            "/register",
            data={
                "username": f"user{i}",
                "password": "password123",
                "email": f"user{i}@example.com",
            },
        )
        assert "Too many registration attempts" not in r.text

    # 6th attempt should be rate limited
    r = client.post(
        "/register",
        data={
            "username": "user6",
            "password": "password123",
            "email": "user6@example.com",
        },
    )
    assert "Too many registration attempts" in r.text


def test_register__disabled_aborts_404(flask_app, client):
    """When registration is disabled, /register returns 404 and create account link is hidden."""
    flask_app.config["ENABLE_REGISTERED_ACCESS"] = False
    try:
        resp = client.get("/register")
        assert resp.status_code == 404

        resp_signin = client.get("/sign-in")
        assert resp_signin.status_code == 200
        assert b'href="/register"' not in resp_signin.data
    finally:
        flask_app.config["ENABLE_REGISTERED_ACCESS"] = True

