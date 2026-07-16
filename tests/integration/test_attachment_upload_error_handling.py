from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="AttachErrorRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="attach_error_user", email="attach_error_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "attach_error_user", "password": "pw123456"})
    return u


def test_upload_without_permission_returns_json_not_html(client, db):
    """Reproduces the reported bug: a permission failure previously
    rendered Flask's default HTML 403 page, which the frontend's
    `r.json()` call can't parse — surfacing only a generic 'Upload
    failed. Please try again.' with no indication it was actually a
    permission problem."""
    _login(client, db, codes=[])  # no attachment.upload permission
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": "1",
    })
    assert resp.status_code == 403
    assert resp.content_type.startswith("application/json")
    data = resp.get_json()
    assert data["ok"] is False
    assert "permission" in data["error"].lower()


def test_upload_with_malformed_reference_id_returns_friendly_json_error(client, db):
    """A malformed/missing reference_id used to raise an unhandled
    ValueError (int(None)), producing an HTML 500 page instead of a
    JSON error the frontend could actually display."""
    _login(client, db, codes=["attachment.upload"])
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles",
        # reference_id intentionally omitted
    })
    assert resp.status_code == 400
    assert resp.content_type.startswith("application/json")
    data = resp.get_json()
    assert data["ok"] is False


def test_upload_with_no_file_returns_friendly_json_error(client, db):
    _login(client, db, codes=["attachment.upload"])
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": "1",
    })
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["ok"] is False
    assert "file" in data["error"].lower()


def test_successful_upload_still_works(client, db, tmp_path):
    import io
    _login(client, db, codes=["attachment.upload"])
    resp = client.post("/master/attachments/upload", data={
        "reference_table": "vehicles", "reference_id": "1",
        "file": (io.BytesIO(b"fake image content"), "test.jpg"),
    }, content_type="multipart/form-data")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["filename"] == "test.jpg"
