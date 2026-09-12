"""Vendor attachments -- confirmed missing entirely from the JSON API.

Flask's Jinja form gets this for free from one fully generic
/master/attachments/upload route keyed by a bare reference_table
string; vendor attachments already work there with zero vendor-specific
code. The JSON API deliberately does not mirror that genericness --
every entity gets its own route pair, gated on its own permission code
and its own visibility check, mirroring vehicle-registrations and
flask-v252's vehicle-handovers exactly.
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.vendor.service import VendorService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def rig(db, app):
    sync_permissions()
    db.session.commit()
    vendor = VendorService().create(code="VEND-001", name="Acme Parts Co.")

    full = Role(name="Vendor Full")
    full.permissions = Permission.query.filter(
        Permission.code.in_(["vendor.view", "vendor.update"])).all()
    view_only = Role(name="Vendor Viewer")
    view_only.permissions = Permission.query.filter_by(
        code="vendor.view").all()

    officer = User(username="officer", email="o@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    officer.roles = [full]
    viewer = User(username="viewer", email="v@e.com",
                 password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [view_only]
    db.session.add_all([officer, viewer])
    db.session.commit()
    return {"vendor": vendor}


def _headers(client, username):
    tok = client.post("/api/v1/auth/token",
                      json={"username": username, "password": "secret123"})
    return {"Authorization": f"Bearer {json.loads(tok.get_data(as_text=True))['access_token']}"}


class TestVendorAttachments:
    def test_view_only_user_can_list(self, client, app, rig):
        headers = _headers(client, "viewer")
        resp = client.get(f"/api/v1/vendors/{rig['vendor'].id}/attachments",
                          headers=headers)
        assert resp.status_code == 200
        body = json.loads(resp.get_data(as_text=True))
        assert body["items"] == []
        # Vendors carry no Document Type, unlike a Maintenance Order --
        # confirms _attachments_allowed_for treats an unmapped table as
        # unrestricted rather than silently blocking every upload.
        assert body["attachment_allowed"] is True

    def test_view_only_user_cannot_upload(self, client, app, rig):
        headers = _headers(client, "viewer")
        resp = client.post(
            f"/api/v1/vendors/{rig['vendor'].id}/attachments",
            data={"file": (io.BytesIO(b"pdf-bytes"), "receipt.pdf")},
            headers=headers, content_type="multipart/form-data")
        assert resp.status_code == 403

    def test_full_user_can_upload_and_then_list_it(self, client, app, rig):
        headers = _headers(client, "officer")
        upload = client.post(
            f"/api/v1/vendors/{rig['vendor'].id}/attachments",
            data={"file": (io.BytesIO(b"pdf-bytes"), "receipt.pdf")},
            headers=headers, content_type="multipart/form-data")
        assert upload.status_code == 201

        listed = client.get(f"/api/v1/vendors/{rig['vendor'].id}/attachments",
                            headers=headers)
        items = json.loads(listed.get_data(as_text=True))["items"]
        assert len(items) == 1
        assert items[0]["filename"] == "receipt.pdf" or "receipt" in json.dumps(items[0])

    def test_no_file_is_a_400_not_a_500(self, client, app, rig):
        headers = _headers(client, "officer")
        resp = client.post(f"/api/v1/vendors/{rig['vendor'].id}/attachments",
                           data={}, headers=headers,
                           content_type="multipart/form-data")
        assert resp.status_code == 400

    def test_nonexistent_vendor_is_404_not_500(self, client, app, rig):
        headers = _headers(client, "officer")
        resp = client.get("/api/v1/vendors/999999/attachments", headers=headers)
        assert resp.status_code == 404

    def test_upload_to_nonexistent_vendor_is_404(self, client, app, rig):
        headers = _headers(client, "officer")
        resp = client.post(
            "/api/v1/vendors/999999/attachments",
            data={"file": (io.BytesIO(b"x"), "a.pdf")},
            headers=headers, content_type="multipart/form-data")
        assert resp.status_code == 404
