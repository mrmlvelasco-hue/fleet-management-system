"""Vehicle Registration API.

Phase 1 scope in the project's master prompt, with only a read-only
history endpoint before this. Built against Flask's real model and
service.

The master prompt's LTO rules are the substance of this module, and
each is tested rather than assumed:

  * Three-year registration for new vehicles, one year for renewals
    (DEFAULT_VALIDITY_YEARS in the service).
  * Conduction Number before Plate Number -- completing a NEW
    registration with a plate assigns it to the vehicle, which is the
    moment a conduction-numbered unit becomes plated.
  * OR and CR numbers are real government document numbers and must be
    unique across every registration; a duplicate almost always means
    the same document was entered twice or copied from another
    vehicle.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def vr_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Registration Clerk")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "vehicleregistration.view", "vehicleregistration.create",
        "vehicleregistration.update", "vehicleregistration.print"])).all()
    user = User(username="regclerk", email="rc@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Registration Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "vehicleregistration.view").all()
    viewer = User(username="regviewer", email="rv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-VR", name="VR Branch")
    vt = VehicleTypeService().create(code="LV-VR", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2026,
        branch_id=branch.id, conduction_number="VR-CN-001")
    _ensure_doc_type("VR")
    db.session.commit()
    return {"user": user, "branch": branch, "vt": vt, "vehicle": vehicle}


def _token(client, username="regclerk"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _post(client, path, token, payload=None):
    r = client.post(path, json=payload or {},
                    headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    return r.status_code, (json.loads(body) if body else {})


def _payload(vr_env, **overrides):
    p = {
        "vehicle_id": vr_env["vehicle"].id,
        "registration_type": "NEW",
        "registration_date": date.today().isoformat(),
        "or_cr_cost": "3500.00",
    }
    p.update(overrides)
    return p


# ── Auth ────────────────────────────────────────────────────────────────────

def test_list_rejects_anonymous(db, client, vr_env):
    assert client.get("/api/v1/vehicle-registrations").status_code == 401


def test_create_requires_the_create_permission(db, client, vr_env):
    status, _ = _post(client, "/api/v1/vehicle-registrations",
                      _token(client, "regviewer"), _payload(vr_env))
    assert status == 403


# ── LTO validity rules ─────────────────────────────────────────────────────

def test_a_new_registration_is_valid_for_three_years(db, client, vr_env):
    """The master prompt's own rule: "Three-year registration for new
    vehicles." Derived by the service from registration_type, not sent
    by the client -- a client that could choose its own validity could
    silently record a non-compliant registration."""
    status, body = _post(client, "/api/v1/vehicle-registrations",
                         _token(client), _payload(vr_env))
    assert status == 201, body
    assert body["validity_years"] == 3
    expected = date.today().replace(year=date.today().year + 3)
    assert body["expiry_date"] == expected.isoformat()


def test_a_renewal_is_valid_for_one_year(db, client, vr_env):
    # A renewal needs a prior registration to renew FROM, so complete a
    # NEW one first -- the service refuses a renewal otherwise, which is
    # itself asserted below.
    t = _token(client)
    _status, first = _post(client, "/api/v1/vehicle-registrations", t,
                           _payload(vr_env))
    _post(client, f"/api/v1/vehicle-registrations/{first['id']}/complete", t,
          {"or_number": "OR-1", "cr_number": "CR-1"})

    status, body = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env, registration_type="RENEWAL"))
    assert status == 201, body
    assert body["validity_years"] == 1


def test_a_renewal_without_a_prior_registration_is_refused(db, client,
                                                            vr_env):
    """There is nothing to renew. Recording one anyway would create a
    registration history with a gap nobody can account for."""
    status, body = _post(client, "/api/v1/vehicle-registrations",
                         _token(client),
                         _payload(vr_env, registration_type="RENEWAL"))
    assert status in (400, 409), body


def test_a_second_active_registration_is_refused(db, client, vr_env):
    """A vehicle cannot hold two overlapping active registrations --
    the second is almost always a duplicate entry."""
    t = _token(client)
    _post(client, "/api/v1/vehicle-registrations", t, _payload(vr_env))
    status, body = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    assert status in (400, 409), body


# ── Completion: OR/CR uniqueness and the plate transition ──────────────────

def test_completing_a_registration_assigns_the_plate_to_the_vehicle(
        db, client, vr_env):
    """The master prompt's "Conduction Number before Plate Number":
    a new unit runs on a conduction number until its plate is issued,
    and completing the registration with that plate is the moment the
    vehicle master gets it."""
    from app.modules.master_data.vehicle.models import Vehicle

    t = _token(client)
    _status, reg = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    status, body = _post(
        client, f"/api/v1/vehicle-registrations/{reg['id']}/complete", t,
        {"or_number": "OR-100", "cr_number": "CR-100",
         "plate_number": "NAO-907"})
    assert status == 200, body
    assert body["status"] == "COMPLETED"

    fresh = Vehicle.query.filter_by(id=vr_env["vehicle"].id).first()
    assert fresh.plate_number == "NAO-907"


def test_a_duplicate_or_number_is_refused(db, client, vr_env):
    """OR numbers are real, physically-issued government documents.
    A duplicate almost always means the same receipt was entered twice
    or copied from another vehicle's record."""
    from app.modules.master_data.vehicle.service import VehicleService

    t = _token(client)
    _status, first = _post(client, "/api/v1/vehicle-registrations", t,
                           _payload(vr_env))
    _post(client, f"/api/v1/vehicle-registrations/{first['id']}/complete", t,
          {"or_number": "OR-DUP", "cr_number": "CR-A"})

    other = VehicleService().create(
        vehicle_type_id=vr_env["vt"].id, brand="Isuzu", model="Elf",
        year=2026, branch_id=vr_env["branch"].id,
        conduction_number="VR-CN-002")
    db.session.commit()
    _status, second = _post(client, "/api/v1/vehicle-registrations", t,
                            _payload(vr_env, vehicle_id=other.id))
    status, body = _post(
        client, f"/api/v1/vehicle-registrations/{second['id']}/complete", t,
        {"or_number": "OR-DUP", "cr_number": "CR-B"})
    assert status == 409, body
    assert "OR" in body.get("message", "")


def test_a_duplicate_cr_number_is_refused(db, client, vr_env):
    from app.modules.master_data.vehicle.service import VehicleService

    t = _token(client)
    _status, first = _post(client, "/api/v1/vehicle-registrations", t,
                           _payload(vr_env))
    _post(client, f"/api/v1/vehicle-registrations/{first['id']}/complete", t,
          {"or_number": "OR-X", "cr_number": "CR-DUP"})

    other = VehicleService().create(
        vehicle_type_id=vr_env["vt"].id, brand="Hino", model="300",
        year=2026, branch_id=vr_env["branch"].id,
        conduction_number="VR-CN-003")
    db.session.commit()
    _status, second = _post(client, "/api/v1/vehicle-registrations", t,
                            _payload(vr_env, vehicle_id=other.id))
    status, body = _post(
        client, f"/api/v1/vehicle-registrations/{second['id']}/complete", t,
        {"or_number": "OR-Y", "cr_number": "CR-DUP"})
    assert status == 409, body


# ── Detail / list ───────────────────────────────────────────────────────────

def test_detail_404s_for_a_missing_registration(db, client, vr_env):
    status, _ = _get(client, "/api/v1/vehicle-registrations/999999",
                     _token(client))
    assert status == 404


def test_list_shows_created_registrations(db, client, vr_env):
    _post(client, "/api/v1/vehicle-registrations", _token(client),
          _payload(vr_env))
    status, body = _get(client, "/api/v1/vehicle-registrations",
                        _token(client))
    assert status == 200
    assert body["items"][0]["registration_type"] == "NEW"


def test_detail_carries_the_checklist(db, client, vr_env):
    _status, reg = _post(client, "/api/v1/vehicle-registrations",
                         _token(client), _payload(vr_env))
    _status, detail = _get(
        client, f"/api/v1/vehicle-registrations/{reg['id']}", _token(client))
    assert "checklist_items" in detail


# ── Expiring: what the dashboard's renewal list needs ──────────────────────

def test_expiring_returns_registrations_inside_the_window(db, client,
                                                           vr_env):
    """Backs the dashboard's "Registration Renewals" tile, which has had
    no real destination in React because this endpoint did not exist."""
    t = _token(client)
    _status, reg = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    _post(client, f"/api/v1/vehicle-registrations/{reg['id']}/complete", t,
          {"or_number": "OR-EXP", "cr_number": "CR-EXP"})

    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    row = VehicleRegistration.query.filter_by(id=reg["id"]).first()
    row.expiry_date = date.today() + timedelta(days=10)
    db.session.commit()

    status, body = _get(client, "/api/v1/vehicle-registrations/expiring",
                        t)
    assert status == 200
    assert any(i["vehicle_id"] == vr_env["vehicle"].id
               for i in body["items"])


def test_expiring_excludes_registrations_far_in_the_future(db, client,
                                                            vr_env):
    t = _token(client)
    _status, reg = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    _post(client, f"/api/v1/vehicle-registrations/{reg['id']}/complete", t,
          {"or_number": "OR-FAR", "cr_number": "CR-FAR"})

    status, body = _get(
        client, "/api/v1/vehicle-registrations/expiring?days_ahead=30", t)
    # The NEW registration created above expires in three years, well
    # outside a 30-day window.
    assert all(i["days_remaining"] <= 30 for i in body["items"])


def test_a_checklist_item_cannot_be_toggled_through_another_registration(
        db, client, vr_env):
    """The service's toggle_checklist_item takes only an item id and
    does not check which registration it belongs to -- the same
    ownership gap invoice and PR line removal had. The URL asserts the
    relationship, so the API must verify it; otherwise
    /vehicle-registrations/5/checklist/99 could tick an item on
    registration 7.

    Written because a mutation check found the guard was in the code
    but covered by no test at all -- removing it broke nothing, which
    is exactly how an untested guard quietly stops working.
    """
    from app.modules.master_data.vehicle.service import VehicleService

    t = _token(client)
    _status, first = _post(client, "/api/v1/vehicle-registrations", t,
                           _payload(vr_env))
    other_vehicle = VehicleService().create(
        vehicle_type_id=vr_env["vt"].id, brand="Foton", model="Tornado",
        year=2026, branch_id=vr_env["branch"].id,
        conduction_number="VR-CN-004")
    db.session.commit()
    _status, second = _post(client, "/api/v1/vehicle-registrations", t,
                            _payload(vr_env, vehicle_id=other_vehicle.id))

    # No checklist items are seeded by default, so one is created
    # directly against the SECOND registration. A skipped test here
    # would assert nothing while looking green, which is worse than no
    # test -- the whole reason this one exists is that the guard was
    # already unprotected.
    from app.modules.transactions.vehicle_registration.models import (
        RegistrationTransactionChecklistItem)

    item = RegistrationTransactionChecklistItem(
        registration_id=second["id"], activity_code="LTO-01",
        activity_description="Emission test", sort_order=0)
    db.session.add(item)
    db.session.commit()

    status, _ = _post(
        client,
        f"/api/v1/vehicle-registrations/{first['id']}/checklist/{item.id}",
        t, {"done": True})
    assert status == 404

    # And the item on the other registration is genuinely untouched.
    _status, second_detail = _get(
        client, f"/api/v1/vehicle-registrations/{second['id']}", t)
    assert second_detail["checklist_items"][0]["is_done"] is False


def test_registration_attachments_upload_and_list(db, client, vr_env):
    """Extends the same generic attachment pattern, including the
    Document Type's Attachment Allowed gate (flask-v178) -- so the VR
    doctype must permit it, exactly as a real deployment configures."""
    import io
    from app.modules.document_config.models import DocumentType

    dt = DocumentType.query.filter_by(code="VR").first()
    dt.attachment_allowed = True
    db.session.commit()

    t = _token(client)
    _status, reg = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    r = client.post(f"/api/v1/vehicle-registrations/{reg['id']}/attachments",
                    data={"file": (io.BytesIO(b"%PDF-1.4"), "or_copy.pdf")},
                    content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 201, r.get_data(as_text=True)

    status, body = _get(
        client, f"/api/v1/vehicle-registrations/{reg['id']}/attachments", t)
    assert status == 200
    assert body["items"][0]["original_filename"] == "or_copy.pdf"
    assert body["attachment_allowed"] is True


def test_registration_attachments_respect_the_doctype_setting(db, client,
                                                               vr_env):
    import io
    from app.modules.document_config.models import DocumentType

    dt = DocumentType.query.filter_by(code="VR").first()
    dt.attachment_allowed = False
    db.session.commit()

    t = _token(client)
    _status, reg = _post(client, "/api/v1/vehicle-registrations", t,
                         _payload(vr_env))
    r = client.post(f"/api/v1/vehicle-registrations/{reg['id']}/attachments",
                    data={"file": (io.BytesIO(b"%PDF-1.4"), "x.pdf")},
                    content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 409
