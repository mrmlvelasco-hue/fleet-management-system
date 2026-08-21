"""Tests for vehicle attachments over the API.

Flask exposes upload / list / download / delete on the vehicle detail and
form screens. React inherits all four.

Two properties matter more than the plumbing:

  * The allowed extensions and size cap are SYSTEM PARAMETERS, not
    constants. React must be told what they are rather than hardcoding a
    guess that drifts the moment an admin changes the setting.
  * Attachments follow the vehicle's visibility. A file list that
    answered for a vehicle the caller cannot see would leak both the
    existence of the vehicle and the names of its documents.
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def att_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Vehicle Attacher")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "vehicle.update"])).all()
    user = User(username="attacher", email="a@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Attach Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    viewer = User(username="attviewer", email="av@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    vt = VehicleTypeService().create(code="LV-A", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-A", name="Attach Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2021,
        branch_id=branch.id, conduction_number="A-001",
        plate_number="ATT-1111")
    db.session.commit()
    return user, vehicle


def _token(client, username="attacher"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _get(client, url, token):
    r = client.get(url, headers=_auth(token))
    return r.status_code, json.loads(r.get_data(as_text=True))


def _upload(client, vid, token, name="cr.pdf", data=b"%PDF-1.4 fake"):
    return client.post(
        f"/api/v1/vehicles/{vid}/attachments",
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
        headers=_auth(token))


# ── Upload rules exposed to the client ──────────────────────────────────────

def test_upload_rules_are_readable(db, client, att_env):
    """Allowed extensions and the size cap come from System Parameters.
    Hardcoding them in React would drift the moment an admin changes the
    setting, and the user would be told a file is fine that the server
    then rejects."""
    token = _token(client)
    status, body = _get(client, "/api/v1/attachments/rules", token)
    assert status == 200
    assert isinstance(body["allowed_extensions"], list)
    assert body["allowed_extensions"]
    assert body["max_size_mb"] > 0


# ── List ────────────────────────────────────────────────────────────────────

def test_list_is_empty_for_a_vehicle_with_no_files(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    status, body = _get(client, f"/api/v1/vehicles/{vehicle.id}/attachments",
                        token)
    assert status == 200
    assert body["items"] == []


def test_uploaded_file_appears_in_the_list(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    assert _upload(client, vehicle.id, token).status_code == 201
    _, body = _get(client, f"/api/v1/vehicles/{vehicle.id}/attachments", token)
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert set(item) >= {"id", "original_filename", "file_size", "mime_type",
                         "uploaded_at", "uploaded_by"}
    assert item["original_filename"] == "cr.pdf"


def test_list_follows_vehicle_visibility(db, client, att_env):
    """A file list for an invisible vehicle would leak both that the
    vehicle exists and what documents it holds."""
    token = _token(client)
    r = client.get("/api/v1/vehicles/999999/attachments", headers=_auth(token))
    assert r.status_code == 404


# ── Permissions ─────────────────────────────────────────────────────────────

def test_upload_requires_update_permission(db, client, att_env):
    """Reading a vehicle must not imply the right to attach documents to
    it."""
    _, vehicle = att_env
    token = _token(client, "attviewer")
    assert _upload(client, vehicle.id, token).status_code == 403


def test_delete_requires_update_permission(db, client, att_env):
    _, vehicle = att_env
    owner = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, owner).get_data(as_text=True))["id"]
    viewer = _token(client, "attviewer")
    r = client.delete(f"/api/v1/attachments/{att_id}", headers=_auth(viewer))
    assert r.status_code == 403


# ── Validation, delegated to the service ────────────────────────────────────

def test_a_disallowed_extension_is_rejected(db, client, att_env):
    """The rule lives in AttachmentService, driven by System Parameters.
    Re-checking it here would create a second definition of an allowed
    file that drifts from the one the Jinja upload enforces."""
    _, vehicle = att_env
    token = _token(client)
    r = _upload(client, vehicle.id, token, name="payload.exe",
                data=b"MZ fake")
    assert r.status_code == 400
    assert json.loads(r.get_data(as_text=True))["error"] == "validation"


def test_upload_without_a_file_is_a_bad_request(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    r = client.post(f"/api/v1/vehicles/{vehicle.id}/attachments",
                    data={}, content_type="multipart/form-data",
                    headers=_auth(token))
    assert r.status_code == 400


# ── Download ────────────────────────────────────────────────────────────────

def test_download_returns_the_stored_bytes(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, token,
                data=b"%PDF-1.4 hello").get_data(as_text=True))["id"]
    r = client.get(f"/api/v1/attachments/{att_id}/download", headers=_auth(token))
    assert r.status_code == 200
    assert r.data == b"%PDF-1.4 hello"


def test_download_sends_the_original_filename(db, client, att_env):
    """The stored name is uniquified; the user gets back what they
    uploaded."""
    _, vehicle = att_env
    token = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, token,
                name="OR-2026.pdf").get_data(as_text=True))["id"]
    r = client.get(f"/api/v1/attachments/{att_id}/download", headers=_auth(token))
    assert "OR-2026.pdf" in r.headers.get("Content-Disposition", "")


# ── Delete ──────────────────────────────────────────────────────────────────

def test_delete_removes_it_from_the_list(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, token).get_data(as_text=True))["id"]
    assert client.delete(f"/api/v1/attachments/{att_id}",
                         headers=_auth(token)).status_code == 200
    _, body = _get(client, f"/api/v1/vehicles/{vehicle.id}/attachments", token)
    assert body["items"] == []


def test_delete_is_a_soft_delete(db, client, att_env):
    """The service deactivates rather than destroys, so the audit trail
    still has something to point at. The API must not turn that into a
    hard delete."""
    from app.core.models.attachment import Attachment
    _, vehicle = att_env
    token = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, token).get_data(as_text=True))["id"]
    client.delete(f"/api/v1/attachments/{att_id}", headers=_auth(token))
    row = db.session.get(Attachment, att_id)
    assert row is not None
    assert row.is_active is False


def test_download_is_refused_for_an_invisible_parent_vehicle(
        db, client, att_env):
    """An attachment carries no scope of its own -- its confidentiality
    is entirely the parent vehicle's. Without this check, knowing an
    attachment id would be enough to read a document belonging to a
    branch the caller cannot see, and the list endpoint's own visibility
    guard would be pointless.
    """
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.modules.master_data.org.service import BranchService

    _, vehicle = att_env
    owner = _token(client)
    att_id = json.loads(
        _upload(client, vehicle.id, owner).get_data(as_text=True))["id"]

    # A user scoped to a DIFFERENT branch, so the vehicle above is
    # outside what they may see.
    other_branch = BranchService().create(code="BR-OTHER", name="Elsewhere")
    outsider = User(username="outsider", email="o@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    role = Role(name="Outsider")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "vehicle.update"])).all()
    outsider.roles = [role]
    db.session.add_all([role, outsider])
    db.session.commit()
    UserOrgScopeService().assign(outsider.id, scope_type="BRANCH",
                                 branch_id=other_branch.id)
    db.session.commit()

    theirs = _token(client, "outsider")
    assert client.get(f"/api/v1/attachments/{att_id}/download",
                      headers=_auth(theirs)).status_code == 404
    assert client.delete(f"/api/v1/attachments/{att_id}",
                         headers=_auth(theirs)).status_code == 404


# ── Document types ──────────────────────────────────────────────────────────

def test_rules_expose_the_document_types(db, client, att_env):
    """The types come from Lookup Maintenance (ATTACHMENT_DOC_TYPE), so
    an admin can add "Deed of Sale" without a frontend deploy. A list
    hardcoded in React would go stale the moment they did."""
    token = _token(client)
    status, body = _get(client, "/api/v1/attachments/rules", token)
    assert status == 200
    assert isinstance(body["document_types"], list)
    for row in body["document_types"]:
        assert set(row) >= {"code", "label"}


def test_upload_records_the_document_type(db, client, att_env):
    _, vehicle = att_env
    token = _token(client)
    _, rules = _get(client, "/api/v1/attachments/rules", token)
    if not rules["document_types"]:
        pytest.skip("no ATTACHMENT_DOC_TYPE lookup rows seeded")
    code = rules["document_types"][0]["code"]

    r = client.post(
        f"/api/v1/vehicles/{vehicle.id}/attachments",
        data={"file": (io.BytesIO(b"%PDF-1.4 x"), "cr.pdf"),
              "document_type": code},
        content_type="multipart/form-data",
        headers=_auth(token))
    assert r.status_code == 201
    assert json.loads(r.get_data(as_text=True))["document_type"] == code

    _, listing = _get(client, f"/api/v1/vehicles/{vehicle.id}/attachments",
                      token)
    assert listing["items"][0]["document_type"] == code


def test_document_type_is_optional(db, client, att_env):
    """Uploading without one is legitimate; the service normalises an
    empty selection to None rather than storing an empty string."""
    _, vehicle = att_env
    token = _token(client)
    r = _upload(client, vehicle.id, token)
    assert r.status_code == 201
    assert json.loads(r.get_data(as_text=True))["document_type"] is None


def test_an_unrecognised_document_type_is_rejected(db, client, att_env):
    """Validated by the service against the lookup. Accepting free text
    would let the field fill with variant spellings that no report could
    then group by."""
    _, vehicle = att_env
    token = _token(client)
    r = client.post(
        f"/api/v1/vehicles/{vehicle.id}/attachments",
        data={"file": (io.BytesIO(b"%PDF-1.4 x"), "cr.pdf"),
              "document_type": "NOT-A-REAL-TYPE"},
        content_type="multipart/form-data",
        headers=_auth(token))
    assert r.status_code == 400
    assert json.loads(r.get_data(as_text=True))["error"] == "validation"
