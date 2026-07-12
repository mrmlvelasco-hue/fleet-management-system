from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService


def _login(client, db, *, codes=()):
    role = Role(name="OrgScopeUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="nabil", email="nabil@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "nabil", "password": "pw123456"})
    return u


def test_org_scope_page_requires_permission(client, db):
    user = _login(client, db)
    resp = client.get(f"/admin/users/{user.id}/org-scope")
    assert resp.status_code == 403


def test_org_scope_page_renders(client, db):
    user = _login(client, db, codes=["user.update"])
    resp = client.get(f"/admin/users/{user.id}/org-scope")
    assert resp.status_code == 200
    assert b"Organizational Scope" in resp.data
    assert b"No scope assigned yet" in resp.data


def test_assign_branch_scope_via_form(client, db):
    user = _login(client, db, codes=["user.update"])
    branch = BranchService().create(code="BR-UISCOPE", name="UI Scope Branch")
    resp = client.post(f"/admin/users/{user.id}/org-scope", data={
        "scope_type": "BRANCH", "branch_id": str(branch.id),
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"UI Scope Branch" in resp.data

    from app.modules.user_management.org_scope_service import UserOrgScopeService
    scopes = UserOrgScopeService().list_for_user(user.id)
    assert len(scopes) == 1
    assert scopes[0].branch_id == branch.id


def test_remove_scope_via_form(client, db):
    user = _login(client, db, codes=["user.update"])
    branch = BranchService().create(code="BR-UISCOPE2", name="UI Scope Branch 2")
    from app.modules.user_management.org_scope_service import UserOrgScopeService
    scope = UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                         branch_id=branch.id)
    resp = client.post(
        f"/admin/users/{user.id}/org-scope/{scope.id}/remove",
        follow_redirects=True)
    assert resp.status_code == 200
    assert UserOrgScopeService().list_for_user(user.id) == []
