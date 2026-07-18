from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vendor.service import VendorService, VendorContactService


def _login(client, db, *, codes=()):
    role = Role(name="VendorContactUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="vendor_contact_ui_user", email="vendor_contact_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "vendor_contact_ui_user", "password": "pw123456"})
    return u


def test_add_contact_via_form(client, db):
    _login(client, db, codes=["vendor.view", "vendor.update"])
    vendor = VendorService().create(code="VEND-CONTACTUI", name="Contact UI Vendor",
                                    vendor_type="SERVICES")
    resp = client.post(f"/master/vendors/{vendor.id}/contacts", data={
        "contact_name": "Maria Santos", "tel_number": "02-1234567",
        "cel_number": "0917-000-0000", "email": "maria@vendor.com",
        "position": "Account Manager",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Maria Santos" in resp.data
    assert b"Account Manager" in resp.data


def test_delete_contact_via_form(client, db):
    _login(client, db, codes=["vendor.view", "vendor.update"])
    vendor = VendorService().create(code="VEND-CONTACTUI2", name="Contact UI Vendor 2",
                                    vendor_type="SERVICES")
    contact = VendorContactService().create(vendor_id=vendor.id, contact_name="To Remove")
    resp = client.post(f"/master/vendors/{vendor.id}/contacts/{contact.id}/delete",
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"To Remove" not in resp.data


def test_other_contact_section_only_shows_for_existing_vendor(client, db):
    _login(client, db, codes=["vendor.view", "vendor.create"])
    resp = client.get("/master/vendors/new")
    assert resp.status_code == 200
    assert b"Other Contact" not in resp.data
