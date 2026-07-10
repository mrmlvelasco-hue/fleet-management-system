from app.core.security.password import hash_password
from app.modules.user_management.models import User


def _make_user(db, username="gina", password="pw123456"):
    u = User(username=username, email=f"{username}@example.com",
             password_hash=hash_password(password))
    db.session.add(u)
    db.session.commit()
    return u


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Username" in resp.data


def test_login_success_redirects_to_dashboard(client, db):
    _make_user(db)
    resp = client.post("/login", data={"username": "gina",
                                       "password": "pw123456"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_login_failure_shows_error(client, db):
    _make_user(db)
    resp = client.post("/login", data={"username": "gina", "password": "bad"})
    assert b"Invalid username or password" in resp.data


def test_dashboard_requires_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_logout(client, db):
    _make_user(db)
    client.post("/login", data={"username": "gina", "password": "pw123456"})
    resp = client.get("/logout", follow_redirects=True)
    assert b"logged out" in resp.data.lower()
