from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vendor.service import VendorService


def _login(client, db, *, codes=()):
    role = Role(name="VendorScopeUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vendorscope_ui", email="vendorscope_ui@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vendorscope_ui", "password": "pw123456"})
    return u


def test_vendor_form_has_searchable_multi_branch_field(client, db):
    _login(client, db, codes=["vendor.view", "vendor.create"])
    resp = client.get("/master/vendors/new")
    assert resp.status_code == 200
    assert b'id="vendorBranchSelect"' in resp.data
    assert b"multiple" in resp.data


def test_create_vendor_serving_multiple_branches(client, db):
    _login(client, db, codes=["vendor.view", "vendor.create"])
    laguna = BranchService().create(code="BR-LAGUI", name="Laguna UI")
    manila = BranchService().create(code="BR-MNLUI", name="Manila UI")

    resp = client.post("/master/vendors/new", data={
        "code": "VEN-LAGMNL", "name": "Laguna-Manila Auto Shop",
        "vendor_type": "SERVICES",
        "branch_ids": [str(laguna.id), str(manila.id)],
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.master_data.vendor.models import Vendor
    vendor = Vendor.query.filter_by(code="VEN-LAGMNL").first()
    assert vendor is not None
    assert {b.id for b in vendor.branches} == {laguna.id, manila.id}


def test_manila_scoped_user_sees_shared_vendor_but_not_cebu_only_one(client, db):
    laguna = BranchService().create(code="BR-LAGUI2", name="Laguna UI 2")
    manila = BranchService().create(code="BR-MNLUI2", name="Manila UI 2")
    cebu = BranchService().create(code="BR-CEBUI2", name="Cebu UI 2")

    shared_vendor = VendorService().create(code="VEN-SHAREDUI", name="Shared Shop UI")
    VendorService().assign_branches(shared_vendor.id, [laguna.id, manila.id])
    cebu_vendor = VendorService().create(code="VEN-CEBUONLYUI", name="Cebu Only UI")
    VendorService().assign_branches(cebu_vendor.id, [cebu.id])

    user = _login(client, db, codes=["vendor.view"])
    UserOrgScopeService().assign(user.id, scope_type="BRANCH", branch_id=manila.id)

    resp = client.get("/master/vendors")
    assert b"Shared Shop UI" in resp.data
    assert b"Cebu Only UI" not in resp.data
