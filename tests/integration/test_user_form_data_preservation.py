from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="UserFormTestRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="tariq_userform", email="tariq_userform@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "tariq_userform", "password": "pw123456"})
    return u


def test_duplicate_username_error_preserves_other_fields(client, db):
    _login(client, db, codes=["user.view", "user.create"])
    from app.modules.user_management.service import UserService
    UserService().create_user(username="existing_user", email="ex@test.com",
                              password="Pw123456!")

    resp = client.post("/admin/users/new", data={
        "username": "existing_user",  # duplicate — will fail
        "email": "newperson@test.com",
        "first_name": "Newbie", "last_name": "Person",
    })
    assert resp.status_code == 200
    html = resp.data.decode()
    assert "already exists" in html.lower()
    # Everything else the user typed should still be there
    assert 'value="newperson@test.com"' in html
    assert 'value="Newbie"' in html
    assert 'value="Person"' in html


def test_duplicate_email_no_longer_crashes_and_succeeds(client, db):
    _login(client, db, codes=["user.view", "user.create"])
    from app.modules.user_management.service import UserService
    UserService().create_user(username="first_user_email", email="shared2@test.com",
                              password="Pw123456!")

    resp = client.post("/admin/users/new", data={
        "username": "second_user_email", "email": "shared2@test.com",
        "first_name": "Second", "last_name": "User",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert User.query.filter_by(username="second_user_email").count() == 1
