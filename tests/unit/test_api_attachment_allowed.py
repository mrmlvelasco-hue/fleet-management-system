"""Attachments must honour the Document Type's "Attachment Allowed".

The master prompt requires each Document Type to define whether
attachments are permitted:

    Each Document Type shall define:
      - Requires Approval (Yes/No)
      - Auto Numbering
      - Printable
      - Mobile Available
      - Attachment Allowed

DocumentType.attachment_allowed exists, is editable in System
Administration, is shown in the doctype list and form, and defaults to
False -- but before this it was enforced NOWHERE. Not in the API, and
not in Flask's own UI either: the shared _attachment_panel.html
include renders unconditionally on every detail screen. So a client
could turn attachments off for a document type and users would still
see the panel and still upload successfully, which makes the setting
worse than absent: it reads as a control that works.

Enforced at the API rather than only hidden in the UI. A hidden panel
still leaves the endpoint open to anything that calls it directly, and
"no values shall be hardcoded" means the rule has to bind where the
write happens.
"""
import io
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _set_attachment_allowed(db, code, allowed):
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code=code).first()
    dt.attachment_allowed = allowed
    db.session.commit()
    return dt


@pytest.fixture()
def aa_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="AA Clerk")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "maintenanceorder.view", "maintenanceorder.create",
        "maintenanceorder.update"])).all()
    user = User(username="aaclerk", email="aa@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    branch = BranchService().create(code="BR-AA", name="AA Branch")
    vt = VehicleTypeService().create(code="LV-AA", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="AA-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="AA-000")

    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="MO",
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code="MO").first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    return {"user": user, "order": order}


def _token(client, username="aaclerk"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _upload(client, order_id, token):
    return client.post(
        f"/api/v1/maintenance-orders/{order_id}/attachments",
        data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "doc.pdf")},
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {token}"})


def test_upload_is_refused_when_the_document_type_disallows_attachments(
        db, client, aa_env):
    _set_attachment_allowed(db, "MO", False)
    r = _upload(client, aa_env["order"].id, _token(client))
    assert r.status_code == 409, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    # The message must name the setting and where to change it, or the
    # person hits a wall with no idea it is a configuration choice.
    assert "attachment" in body["message"].lower()


def test_upload_succeeds_when_the_document_type_allows_attachments(
        db, client, aa_env):
    _set_attachment_allowed(db, "MO", True)
    r = _upload(client, aa_env["order"].id, _token(client))
    assert r.status_code == 201, r.get_data(as_text=True)


def test_listing_reports_whether_attachments_are_allowed(db, client, aa_env):
    """The client needs to know BEFORE offering an upload control --
    rendering "Attach file" and then rejecting the upload is a worse
    experience than not offering it."""
    _set_attachment_allowed(db, "MO", False)
    r = client.get(f"/api/v1/maintenance-orders/{aa_env['order'].id}/attachments",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["attachment_allowed"] is False

    _set_attachment_allowed(db, "MO", True)
    r2 = client.get(f"/api/v1/maintenance-orders/{aa_env['order'].id}/attachments",
                    headers={"Authorization": f"Bearer {_token(client)}"})
    assert json.loads(r2.get_data(as_text=True))["attachment_allowed"] is True


def test_existing_attachments_stay_readable_after_the_setting_is_turned_off(
        db, client, aa_env):
    """Turning the setting off must not orphan documents already
    uploaded under it. Blocking NEW uploads is a policy change; hiding
    evidence already attached to a record is data loss."""
    t = _token(client)
    _set_attachment_allowed(db, "MO", True)
    assert _upload(client, aa_env["order"].id, t).status_code == 201

    _set_attachment_allowed(db, "MO", False)
    r = client.get(f"/api/v1/maintenance-orders/{aa_env['order'].id}/attachments",
                   headers={"Authorization": f"Bearer {t}"})
    body = json.loads(r.get_data(as_text=True))
    assert len(body["items"]) == 1
    assert body["attachment_allowed"] is False


def test_a_module_with_no_document_type_configured_still_allows_attachments(
        db, client, aa_env):
    """Master-data records (vehicles, drivers, tires, batteries) have no
    DocumentType at all -- the setting is a TRANSACTION document
    concept. Defaulting those to "not allowed" would silently break
    every master-data attachment panel that has worked all along.
    Absent configuration means unrestricted, not forbidden.
    """
    from app.modules.api.attachments import _attachments_allowed_for

    assert _attachments_allowed_for("vehicles") is True
    assert _attachments_allowed_for("drivers") is True
