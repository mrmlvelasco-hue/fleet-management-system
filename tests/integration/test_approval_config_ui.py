from app.core.security.password import hash_password
from app.modules.approval_config.models import ApprovalPath
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, permission_codes=()):
    role = Role(name="ApprCfgRole")
    for code in permission_codes:
        module, action = code.split(".")
        perm = Permission(code=code, module=module, action=action)
        db.session.add(perm)
        role.permissions.append(perm)
    user = User(username="jack", email="j@example.com",
                password_hash=hash_password("pw123456"))
    user.roles.append(role)
    db.session.add_all([role, user])
    db.session.commit()
    return user


def _signin(client):
    client.post("/login", data={"username": "jack", "password": "pw123456"})


def test_paths_403_without_permission(client, db):
    _login(client, db)
    _signin(client)
    assert client.get("/admin/approval-paths").status_code == 403


def test_paths_200_with_permission(client, db):
    _login(client, db, permission_codes=["approvalpath.view"])
    _signin(client)
    assert client.get("/admin/approval-paths").status_code == 200


def test_create_path_via_post(client, db):
    user = _login(client, db, permission_codes=[
        "approvalpath.view", "approvalpath.create"])
    approver_role = Role(name="Checker")
    db.session.add(approver_role)
    db.session.commit()
    _signin(client)
    resp = client.post("/admin/approval-paths/new", data={
        "name": "One-Step",
        "description": "Single checker",
        "level_approver_type": ["ROLE"],
        "level_role_id": [str(approver_role.id)],
        "level_user_id": [""],
    }, follow_redirects=True)
    assert resp.status_code == 200
    path = ApprovalPath.query.filter_by(name="One-Step").first()
    assert path is not None
    assert len(path.levels) == 1
    assert path.levels[0].role_id == approver_role.id


def test_matrix_pages(client, db):
    _login(client, db, permission_codes=[
        "approvalmatrix.view", "approvalmatrix.create"])
    _signin(client)
    assert client.get("/admin/approval-matrix").status_code == 200
    assert client.get("/admin/approval-matrix/new").status_code == 200
