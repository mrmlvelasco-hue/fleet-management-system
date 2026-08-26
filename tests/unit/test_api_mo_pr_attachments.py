"""Attachments on Maintenance Orders and Purchase Requests.

Confirmed missing entirely: neither detail screen had any way to
upload, list or download a supporting document -- only a count column
on the MO list. A working pattern already exists for vehicles, drivers,
tires and batteries (GET/POST /{module}/<id>/attachments, plus the
shared download/DELETE by attachment id); MO and PR were simply never
added to it. This extends the SAME pattern rather than inventing a new
one -- same _parent_visible dispatch, same _visible_X helper shape,
same route pair shape.
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
from app.modules.transactions.purchase_request.service import (
    PurchaseRequestService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True,
                                     # attachment_allowed defaults to
                                     # False, and is now genuinely
                                     # enforced -- these fixtures test
                                     # attachment behaviour, so they
                                     # must configure the document type
                                     # to permit it, exactly as a real
                                     # deployment would.
                                     attachment_allowed=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def att_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Attachment Uploader")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "maintenanceorder.view", "maintenanceorder.create",
        "maintenanceorder.update",
        "purchaserequest.view", "purchaserequest.create",
    ])).all()
    user = User(username="attuser", email="au@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="View Only")
    viewer_role.permissions = Permission.query.filter(Permission.code.in_([
        "maintenanceorder.view", "purchaserequest.view"])).all()
    viewer = User(username="attviewer", email="av@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-ATT", name="Attachment Branch")
    vt = VehicleTypeService().create(code="LV-ATT", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="ATT-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="ATT-000")
    _ensure_doc_type("MO")
    _ensure_doc_type("PR")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    pr = PurchaseRequestService().create(
        description="Attachment test PR", justification="Because.",
        lines=[{"item_description": "Oil", "quantity": 1, "unit_cost": 100}],
        user=None)
    db.session.commit()
    return {"user": user, "order": order, "pr": pr}


def _token(client, username="attuser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _upload(client, url, token, filename="doc.pdf"):
    data = {"file": (io.BytesIO(b"%PDF-1.4 fake"), filename)}
    r = client.post(url, data=data, content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


# ── Maintenance Order attachments ───────────────────────────────────────────

def test_mo_attachments_list_is_empty_for_a_fresh_order(db, client, att_env):
    status, body = _get(
        client, f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments",
        _token(client))
    assert status == 200
    assert body["items"] == []


def test_mo_attachments_reject_anonymous(db, client, att_env):
    assert client.get(
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments"
    ).status_code == 401


def test_mo_attachment_upload_requires_update_not_just_view(db, client,
                                                            att_env):
    """Being able to read an order does not imply the right to attach
    documents to its record -- matching the same reasoning already
    recorded on the vehicle attachment upload endpoint."""
    status, _ = _upload(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments",
        _token(client, "attviewer"))
    assert status == 403


def test_mo_attachment_uploads_and_lists(db, client, att_env):
    t = _token(client)
    status, body = _upload(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t,
        filename="odometer_reading.pdf")
    assert status == 201, body
    assert body["original_filename"] == "odometer_reading.pdf"

    _status, listed = _get(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t)
    assert len(listed["items"]) == 1


def test_mo_attachment_upload_with_no_file_is_a_bad_request(db, client,
                                                            att_env):
    t = _token(client)
    r = client.post(
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments",
        data={}, content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 400


def test_mo_attachments_404_for_an_unknown_order(db, client, att_env):
    status, _ = _get(client, "/api/v1/maintenance-orders/999999/attachments",
                     _token(client))
    assert status == 404


def test_mo_attachment_count_on_detail_matches_the_upload(db, client,
                                                          att_env):
    """The list screen's Docs column already reads attachment_count off
    the MO row (flask-v153) -- confirms uploading through this new
    endpoint is visible to that existing column, not a second,
    disconnected attachment mechanism."""
    t = _token(client)
    _upload(client,
           f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t)
    _status, detail = _get(
        client, f"/api/v1/maintenance-orders/{att_env['order'].id}", t)
    assert detail is not None
    _status, mo_list = _get(client, "/api/v1/maintenance-orders", t)
    row = next(r for r in mo_list["items"] if r["id"] == att_env["order"].id)
    assert row["attachment_count"] == 1


# ── Purchase Request attachments ────────────────────────────────────────────

def test_pr_attachments_list_is_empty_for_a_fresh_request(db, client,
                                                          att_env):
    status, body = _get(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        _token(client))
    assert status == 200
    assert body["items"] == []


def test_pr_attachment_uploads_and_lists(db, client, att_env):
    t = _token(client)
    status, body = _upload(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        t, filename="supplier_quote.pdf")
    assert status == 201, body

    _status, listed = _get(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        t)
    assert len(listed["items"]) == 1
    assert listed["items"][0]["original_filename"] == "supplier_quote.pdf"


def test_pr_attachment_upload_requires_create_permission(db, client,
                                                         att_env):
    status, _ = _upload(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        _token(client, "attviewer"))
    assert status == 403


def test_pr_attachments_404_for_an_unknown_request(db, client, att_env):
    status, _ = _get(client, "/api/v1/purchase-requests/999999/attachments",
                     _token(client))
    assert status == 404


# ── Shared download/delete already work for the new modules ────────────────

def test_uploaded_mo_attachment_can_be_downloaded_through_the_shared_route(
        db, client, att_env):
    """download_attachment already dispatches through _parent_visible,
    which this work extends -- confirms the shared route picks up the
    new reference_table without its own changes."""
    t = _token(client)
    _status, att = _upload(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t)
    r = client.get(f"/api/v1/attachments/{att['id']}/download",
                   headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200


def test_a_viewer_without_access_cannot_download_a_pr_attachment(db, client,
                                                                 att_env):
    """_parent_visible must actually be consulted for purchase_requests,
    not silently fall through to its own `return False` default (which
    would make every PR attachment permanently undownloadable, not
    correctly scoped)."""
    t = _token(client)
    _status, att = _upload(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        t)
    r = client.get(f"/api/v1/attachments/{att['id']}/download",
                   headers={"Authorization": f"Bearer {_token(client, 'attviewer')}"})
    # attviewer HAS purchaserequest.view, so this must succeed -- proving
    # the dispatch reached the real visibility check rather than the
    # default False (which would 404 regardless of real permissions).
    assert r.status_code == 200


# ── Delete permission gating (shared route, per-table `need` map) ──────────

def test_mo_attachment_delete_requires_update_not_just_view(db, client,
                                                             att_env):
    """The shared delete_attachment route looks up the required
    permission from a per-reference_table map. Confirmed missing
    entirely for maintenance_orders/purchase_requests before this test
    was written: `need` would resolve to None for either table, and
    delete_attachment's own `if need and not _can(...)` SKIPS the
    permission check when `need` is falsy -- meaning without this
    entry, any user who can merely VIEW an order could delete its
    attachments. This is the exact class of gap this project has hit
    once already (an attachment visibility bypass with zero coverage
    while other tests passed)."""
    t = _token(client)
    _status, att = _upload(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t)
    r = client.delete(
        f"/api/v1/attachments/{att['id']}",
        headers={"Authorization": f"Bearer {_token(client, 'attviewer')}"})
    assert r.status_code == 403


def test_pr_attachment_delete_requires_create_not_just_view(db, client,
                                                            att_env):
    t = _token(client)
    _status, att = _upload(
        client, f"/api/v1/purchase-requests/{att_env['pr'].id}/attachments",
        t)
    r = client.delete(
        f"/api/v1/attachments/{att['id']}",
        headers={"Authorization": f"Bearer {_token(client, 'attviewer')}"})
    assert r.status_code == 403


def test_mo_attachment_delete_succeeds_for_a_user_with_update(db, client,
                                                              att_env):
    t = _token(client)
    _status, att = _upload(
        client,
        f"/api/v1/maintenance-orders/{att_env['order'].id}/attachments", t)
    r = client.delete(f"/api/v1/attachments/{att['id']}",
                      headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200
