from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.transactions.maintenance_order.service import (
    TransactionTypeService)


def _login(client, db, *, codes=()):
    role = Role(name="MOTTAdminUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mott_admin_ui_user", email="mott_admin_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mott_admin_ui_user", "password": "pw123456"})
    return u


def test_list_page_renders(client, db):
    _login(client, db, codes=["motransactiontype.view"])
    TransactionTypeService().create(code="TEST-TT-1", name="Test Type",
                                    order_category="OPERATIONAL", group="DEPLOYMENT")
    resp = client.get("/admin/mo-transaction-types")
    assert resp.status_code == 200
    assert b"Test Type" in resp.data


def test_create_transaction_type_via_form(client, db):
    _login(client, db, codes=["motransactiontype.view", "motransactiontype.create"])
    resp = client.post("/admin/mo-transaction-types/new", data={
        "code": "TEST-TT-2", "name": "New Test Type",
        "order_category": "MAINTENANCE", "group": "MAINTENANCE", "sort_order": "5",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"New Test Type" in resp.data


def test_deactivate_and_reactivate(client, db):
    _login(client, db, codes=["motransactiontype.view", "motransactiontype.create",
                              "motransactiontype.delete"])
    tt = TransactionTypeService().create(code="TEST-TT-3", name="Toggle Test",
                                         order_category="OPERATIONAL")
    client.post(f"/admin/mo-transaction-types/{tt.id}/deactivate")
    updated = TransactionTypeService().get_by_id(tt.id)
    assert updated.is_active is False

    client.post(f"/admin/mo-transaction-types/{tt.id}/reactivate")
    updated = TransactionTypeService().get_by_id(tt.id)
    assert updated.is_active is True


def test_sidebar_shows_link(client, db):
    _login(client, db, codes=["motransactiontype.view"])
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"MO Transaction Types" in resp.data
