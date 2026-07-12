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
