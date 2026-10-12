import json

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="AuditByRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="audit_actor", email="audit_actor@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "audit_actor", "password": "pw123456"})
    return u


def test_created_by_auto_populated_via_real_request(client, db):
    user = _login(client, db, codes=["branch.view", "branch.create"])
    client.post("/master/branches/new", data={
        "code": "BR-AUDITBY", "name": "Audit By Branch",
    }, follow_redirects=True)

    from app.modules.master_data.org.models import Branch
    branch = Branch.query.filter_by(code="BR-AUDITBY").first()
    assert branch is not None
    assert branch.created_by == user.id
    assert branch.updated_by == user.id


def test_updated_by_refreshed_on_update_via_real_request(client, db):
    user = _login(client, db, codes=["branch.view", "branch.create", "branch.update"])
    client.post("/master/branches/new", data={
        "code": "BR-AUDITBY2", "name": "Audit By Branch 2",
    }, follow_redirects=True)
    from app.modules.master_data.org.models import Branch
    branch = Branch.query.filter_by(code="BR-AUDITBY2").first()

    other_user = User(username="other_editor", email="other_editor@x.com",
                      password_hash=hash_password("pw123456"))
    role2 = Role(name="AuditByRole2")
    for code in ["branch.view", "branch.update"]:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        role2.permissions.append(p)
    other_user.roles.append(role2)
    db.session.add_all([role2, other_user])
    db.session.commit()
    client.get("/logout")
    client.post("/login", data={"username": "other_editor", "password": "pw123456"})

    client.post(f"/master/branches/{branch.id}/edit", data={
        "name": "Renamed by other user",
    }, follow_redirects=True)

    db.session.refresh(branch)
    assert branch.updated_by == other_user.id
    assert branch.created_by == user.id  # unchanged


# ── KI-21: the React/API path, which authenticates via JWT bearer tokens
# rather than the Flask-Login session the two tests above exercise. The
# audit hook only ever checked flask_login.current_user, which is the
# Anonymous user for every API request -- so created_by/updated_by (and
# AuditLog.user_id) stayed NULL for every change made through the web app.


def _api_user(db, *, username, codes):
    role = Role(name=f"AuditByAPIRole-{username}")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@x.com",
             password_hash=hash_password("pw123456"), is_active=True)
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    return u


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "pw123456"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def test_created_by_auto_populated_via_api_request(client, db):
    user = _api_user(db, username="api_audit_actor",
                     codes=["branch.view", "branch.create"])
    token = _token(client, "api_audit_actor")

    resp = client.post("/api/v1/branches",
                       json={"code": "BR-APIAUDIT", "name": "API Audit Branch"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201

    from app.modules.master_data.org.models import Branch
    from app.core.models.audit_log import AuditLog
    branch = Branch.query.filter_by(code="BR-APIAUDIT").first()
    assert branch is not None
    assert branch.created_by == user.id
    assert branch.updated_by == user.id

    log = AuditLog.query.filter_by(table_name="branches",
                                   record_id=branch.id, action="CREATE").first()
    assert log is not None
    assert log.user_id == user.id


def test_updated_by_refreshed_on_api_update(client, db):
    user = _api_user(db, username="api_audit_creator",
                     codes=["branch.view", "branch.create"])
    other = _api_user(db, username="api_audit_editor",
                      codes=["branch.view", "branch.update"])

    token = _token(client, "api_audit_creator")
    resp = client.post("/api/v1/branches",
                       json={"code": "BR-APIAUDIT2", "name": "API Audit Branch 2"},
                       headers={"Authorization": f"Bearer {token}"})
    branch_id = json.loads(resp.get_data(as_text=True))["id"]

    other_token = _token(client, "api_audit_editor")
    resp = client.put(f"/api/v1/branches/{branch_id}",
                      json={"name": "Renamed via API"},
                      headers={"Authorization": f"Bearer {other_token}"})
    assert resp.status_code == 200

    from app.modules.master_data.org.models import Branch
    from app.core.models.audit_log import AuditLog
    branch = db.session.get(Branch, branch_id)
    assert branch.created_by == user.id       # unchanged
    assert branch.updated_by == other.id

    log = AuditLog.query.filter_by(table_name="branches", record_id=branch_id,
                                   action="UPDATE").order_by(AuditLog.id.desc()).first()
    assert log is not None
    assert log.user_id == other.id
