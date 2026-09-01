"""Vehicle Movement API.

Phase 1 scope in the project's master prompt, with zero API coverage
before this. Built against Flask's real model and service.

The rule that shapes this module: movement_type is validated against
the MOVEMENT_TYPE Lookup, not a hardcoded list -- the master prompt's
"All business rules must be configurable through System
Administration. No values shall be hardcoded." The service falls back
to code-registered defaults on a fresh install where `flask seed all`
has not run, so a new deployment is usable before seeding. Both paths
are exercised here, because a configurable rule tested only in its
default state is one nobody has verified is configurable.
"""
import json
from datetime import date, datetime, timedelta

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
                                     auto_numbering=True,
                                     attachment_allowed=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def vm_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Movement Clerk")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "vehiclemovement.view", "vehiclemovement.create",
        "vehiclemovement.update", "vehiclemovement.print"])).all()
    user = User(username="mvclerk", email="mv@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Movement Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "vehiclemovement.view").all()
    viewer = User(username="mvviewer", email="mvv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-VM", name="VM Branch")
    vt = VehicleTypeService().create(code="LV-VM", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2024,
        branch_id=branch.id, conduction_number="VM-000")
    _ensure_doc_type("VM")
    db.session.commit()
    return {"user": user, "branch": branch, "vt": vt, "vehicle": vehicle}


def _token(client, username="mvclerk"):
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


def _payload(vm_env, **overrides):
    p = {
        "vehicle_id": vm_env["vehicle"].id,
        "movement_type": "TRANSFER",
        "from_location": "Manila Hub",
        "to_location": "Cebu Hub",
        "movement_date": date.today().isoformat(),
        "purpose": "Branch reallocation",
    }
    p.update(overrides)
    return p


# ── Auth ────────────────────────────────────────────────────────────────────

def test_list_rejects_anonymous(db, client, vm_env):
    assert client.get("/api/v1/vehicle-movements").status_code == 401


def test_create_requires_the_create_permission(db, client, vm_env):
    status, _ = _post(client, "/api/v1/vehicle-movements",
                      _token(client, "mvviewer"), _payload(vm_env))
    assert status == 403


def test_submit_succeeds_on_a_fresh_draft(db, client, vm_env):
    """Regression: the shared _lifecycle dispatcher forwarded remarks
    to every action including submit(), which only ever accepted
    (record_id, user). Raised "submit() got an unexpected keyword
    argument 'remarks'" on every submit call unconditionally -- the
    client sends an empty body -- masked as an ordinary 409 by the
    dispatcher's generic except Exception -> _conflict(str(exc)). No
    submit test existed for this module before this."""
    _status, body = _post(client, "/api/v1/vehicle-movements",
                          _token(client), _payload(vm_env))
    status, resp = _post(
        client, f"/api/v1/vehicle-movements/{body['id']}/submit",
        _token(client))
    assert status == 200, resp


# ── movement_type comes from a Lookup, not a hardcoded list ────────────────

def test_a_registered_movement_type_is_accepted(db, client, vm_env):
    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env))
    assert status == 201, body
    assert body["movement_type"] == "TRANSFER"


def test_an_unknown_movement_type_is_refused_and_names_the_valid_ones(
        db, client, vm_env):
    """The error must list what IS valid. A bare "invalid type" leaves
    the person guessing at a set that is configurable and therefore
    not knowable from the code."""
    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env, movement_type="TELEPORT"))
    assert status == 400, body
    assert "TRANSFER" in json.dumps(body)


def test_a_movement_type_added_as_a_lookup_becomes_valid(db, client, vm_env):
    """The master prompt's rule in practice: "All business rules must
    be configurable through System Administration. No values shall be
    hardcoded." A type the client adds must work without a code change
    -- this is the half that a defaults-only test would never catch.
    """
    from app.modules.system_admin.services.lookup_service import LookupService

    LookupService().create(lookup_type="MOVEMENT_TYPE", code="REPOSSESSION",
                           description="Repossession", sort_order=9)
    db.session.commit()

    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env, movement_type="REPOSSESSION"))
    assert status == 201, body
    assert body["movement_type"] == "REPOSSESSION"


def test_movement_types_are_listed_for_the_form(db, client, vm_env):
    """The form must render the configured set, not a hardcoded
    dropdown that drifts from what the service will accept."""
    status, body = _get(client, "/api/v1/vehicle-movements/form-options",
                        _token(client))
    assert status == 200
    codes = {t["code"] for t in body["movement_types"]}
    assert "TRANSFER" in codes


# ── Create validation ───────────────────────────────────────────────────────

def test_from_and_to_locations_are_required(db, client, vm_env):
    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env, from_location=""))
    assert status == 400, body


def test_movement_date_is_required(db, client, vm_env):
    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env, movement_date=""))
    assert status == 400, body


def test_the_driver_defaults_to_the_vehicles_assigned_driver(db, client,
                                                              vm_env):
    """The service's own behaviour: a movement without an explicit
    driver inherits whoever the vehicle is assigned to, rather than
    recording nobody responsible for a vehicle in transit."""
    from app.modules.master_data.driver.models import Driver

    driver = Driver(first_name="Juan", last_name="Cruz",
                    employee_number="VM-EMP-1",
                    branch_id=vm_env["branch"].id, assignee_type="DRIVER",
                    is_active=True)
    db.session.add(driver)
    db.session.flush()
    vm_env["vehicle"].assigned_driver_id = driver.id
    db.session.commit()

    status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                         _payload(vm_env))
    assert status == 201, body
    assert body["driver_id"] == driver.id


def test_an_explicit_driver_overrides_the_vehicles_assignment(db, client,
                                                               vm_env):
    from app.modules.master_data.driver.models import Driver

    assigned = Driver(first_name="Assigned", last_name="Driver",
                      employee_number="VM-EMP-2",
                      branch_id=vm_env["branch"].id, assignee_type="DRIVER",
                      is_active=True)
    specific = Driver(first_name="Specific", last_name="Driver",
                      employee_number="VM-EMP-3",
                      branch_id=vm_env["branch"].id, assignee_type="DRIVER",
                      is_active=True)
    db.session.add_all([assigned, specific])
    db.session.flush()
    vm_env["vehicle"].assigned_driver_id = assigned.id
    db.session.commit()

    _status, body = _post(client, "/api/v1/vehicle-movements", _token(client),
                          _payload(vm_env, driver_id=specific.id))
    assert body["driver_id"] == specific.id


# ── Detail / list ───────────────────────────────────────────────────────────

def test_detail_404s_for_a_missing_movement(db, client, vm_env):
    status, _ = _get(client, "/api/v1/vehicle-movements/999999",
                     _token(client))
    assert status == 404


def test_list_shows_created_movements(db, client, vm_env):
    _post(client, "/api/v1/vehicle-movements", _token(client),
          _payload(vm_env))
    status, body = _get(client, "/api/v1/vehicle-movements", _token(client))
    assert status == 200
    assert body["items"][0]["to_location"] == "Cebu Hub"


# ── Lifecycle ───────────────────────────────────────────────────────────────

def test_lifecycle_actions_are_routed(db, client, vm_env):
    _status, mv = _post(client, "/api/v1/vehicle-movements", _token(client),
                        _payload(vm_env))
    for action in ("submit", "start-transit", "cancel"):
        status, _ = _post(
            client, f"/api/v1/vehicle-movements/{mv['id']}/{action}",
            _token(client))
        assert status != 404, f"/{action} is not routed"


def test_start_transit_then_complete_records_the_end_time(db, client,
                                                           vm_env):
    t = _token(client)
    _status, mv = _post(client, "/api/v1/vehicle-movements", t,
                        _payload(vm_env))
    _post(client, f"/api/v1/vehicle-movements/{mv['id']}/start-transit", t)

    ended = datetime.now().replace(microsecond=0).isoformat()
    status, body = _post(
        client, f"/api/v1/vehicle-movements/{mv['id']}/complete", t,
        {"movement_end_datetime": ended})
    assert status == 200, body
    assert body["status"] == "COMPLETED"
    assert body["movement_end_datetime"] is not None


def test_completing_without_an_end_time_still_completes(db, client, vm_env):
    """The service treats the end time as optional -- a movement can be
    closed out after the fact without inventing a timestamp nobody
    recorded."""
    t = _token(client)
    _status, mv = _post(client, "/api/v1/vehicle-movements", t,
                        _payload(vm_env))
    status, body = _post(
        client, f"/api/v1/vehicle-movements/{mv['id']}/complete", t, {})
    assert status == 200, body
    assert body["status"] == "COMPLETED"


def test_movement_attachments_upload_and_list(db, client, vm_env):
    """Same generic pattern as every other module, including the
    Attachment Allowed gate and the delete permission map entry --
    that map entry is easy to omit, and the delete check SKIPS
    enforcement entirely when it resolves to None."""
    import io

    t = _token(client)
    _status, mv = _post(client, "/api/v1/vehicle-movements", t,
                        _payload(vm_env))
    r = client.post(f"/api/v1/vehicle-movements/{mv['id']}/attachments",
                    data={"file": (io.BytesIO(b"%PDF-1.4"), "gatepass.pdf")},
                    content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 201, r.get_data(as_text=True)

    status, body = _get(
        client, f"/api/v1/vehicle-movements/{mv['id']}/attachments", t)
    assert status == 200
    assert body["items"][0]["original_filename"] == "gatepass.pdf"


def test_movement_attachment_delete_requires_update_not_just_view(
        db, client, vm_env):
    import io

    t = _token(client)
    _status, mv = _post(client, "/api/v1/vehicle-movements", t,
                        _payload(vm_env))
    r = client.post(f"/api/v1/vehicle-movements/{mv['id']}/attachments",
                    data={"file": (io.BytesIO(b"%PDF-1.4"), "x.pdf")},
                    content_type="multipart/form-data",
                    headers={"Authorization": f"Bearer {t}"})
    att = json.loads(r.get_data(as_text=True))

    d = client.delete(f"/api/v1/attachments/{att['id']}",
                      headers={"Authorization": f"Bearer {_token(client, 'mvviewer')}"})
    assert d.status_code == 403
