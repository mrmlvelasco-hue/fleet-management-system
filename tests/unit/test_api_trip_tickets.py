"""Trip Ticket API.

Phase 1 scope in the project's own master prompt, with zero API
coverage before this. Built against Flask's real model, service and
templates rather than a mockup.

The module's defining rule, straight from the master prompt and
implemented in TripTicketService.create: the system parameter
REQUIRE_DRIVER_FROM_MASTER decides whether the driver must come from
Driver Master (YES) or may be typed free-hand (NO, creating no master
record). Both paths are exercised here, because a configurable rule
that is only ever tested in its default state is a rule nobody has
actually verified is configurable.
"""
import json
from datetime import datetime, timedelta

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


def _set_param(db, code, value):
    from app.modules.system_admin.models import SystemParameter
    row = SystemParameter.query.filter_by(code=code).first()
    if row is None:
        row = SystemParameter(code=code, value=value, data_type="STRING")
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()


@pytest.fixture()
def tt_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Trip Clerk")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "tripticket.view", "tripticket.create", "tripticket.update",
        "tripticket.print"])).all()
    user = User(username="tripclerk", email="tc@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    viewer_role = Role(name="Trip Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code == "tripticket.view").all()
    viewer = User(username="tripviewer", email="tv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]
    db.session.add_all([role, user, viewer_role, viewer])

    branch = BranchService().create(code="BR-TT", name="TT Branch")
    vt = VehicleTypeService().create(code="LV-TT", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="TT-000",
        current_odometer=5000)
    _ensure_doc_type("TT")
    db.session.commit()
    return {"user": user, "branch": branch, "vt": vt, "vehicle": vehicle}


def _token(client, username="tripclerk"):
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


def _payload(tt_env, **overrides):
    p = {
        "vehicle_id": tt_env["vehicle"].id,
        "destination": "Carmona Plant",
        "purpose": "Feed delivery",
        "departure_datetime": (datetime.now() + timedelta(hours=1))
            .replace(microsecond=0).isoformat(),
        "odometer_out": 5000,
        "passengers": "J. Cruz, M. Santos",
    }
    p.update(overrides)
    return p


def _make_driver(db, tt_env, name="Juan Driver"):
    from app.modules.master_data.driver.models import Driver
    first, last = name.split(" ", 1)
    d = Driver(first_name=first, last_name=last, employee_number=f"E-{name}",
               branch_id=tt_env["branch"].id, assignee_type="DRIVER",
               is_active=True)
    db.session.add(d)
    db.session.commit()
    return d


# ── Auth ────────────────────────────────────────────────────────────────────

def test_list_rejects_anonymous(db, client, tt_env):
    assert client.get("/api/v1/trip-tickets").status_code == 401


def test_create_requires_the_create_permission(db, client, tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    status, _ = _post(client, "/api/v1/trip-tickets",
                      _token(client, "tripviewer"),
                      _payload(tt_env, driver_name_manual="Ad Hoc"))
    assert status == 403


# ── REQUIRE_DRIVER_FROM_MASTER: the module's defining rule ─────────────────

def test_driver_must_come_from_master_when_the_parameter_is_yes(db, client,
                                                                 tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "YES")
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env, driver_name_manual="Typed Name"))
    assert status == 400, body
    assert "driver" in json.dumps(body).lower()


def test_master_driver_is_accepted_when_the_parameter_is_yes(db, client,
                                                              tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "YES")
    driver = _make_driver(db, tt_env)
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env, driver_id=driver.id))
    assert status == 201, body
    assert body["driver"] is not None


def test_manual_driver_name_is_accepted_when_the_parameter_is_no(db, client,
                                                                  tt_env):
    """The master prompt's own words: "The Driver is manually entered.
    No master record shall be created." Asserts the second half too --
    a manual entry that quietly created a Driver row would defeat the
    entire point of the setting."""
    from app.modules.master_data.driver.models import Driver

    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    before = Driver.query.count()
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env, driver_name_manual="Ad Hoc Driver"))
    assert status == 201, body
    assert body["driver"] == "Ad Hoc Driver"
    assert Driver.query.count() == before


def test_a_manual_name_is_still_required_when_no_master_driver_is_given(
        db, client, tt_env):
    """Manual mode means typed, not absent -- a trip ticket with no
    driver at all names nobody accountable for the vehicle."""
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env))
    assert status == 400, body


# ── Create validation ───────────────────────────────────────────────────────

def test_destination_and_purpose_are_required(db, client, tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env, destination="",
                                  driver_name_manual="X"))
    assert status == 400, body


def test_odometer_out_is_coerced_from_a_string(db, client, tt_env):
    """The client sends every value as text. odometer_out is an Integer
    column -- the same coercion gap that produced a 500 on invoices'
    vat_percentage before the shared Coercer covered it."""
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    status, body = _post(client, "/api/v1/trip-tickets", _token(client),
                         _payload(tt_env, odometer_out="5200",
                                  driver_name_manual="X"))
    assert status == 201, body
    assert body["odometer_out"] == 5200


def test_a_non_numeric_odometer_is_a_field_error_not_a_500(db, client,
                                                            tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    status, _ = _post(client, "/api/v1/trip-tickets", _token(client),
                      _payload(tt_env, odometer_out="five thousand",
                               driver_name_manual="X"))
    assert status == 400


# ── Detail / list ───────────────────────────────────────────────────────────

def test_detail_404s_for_a_missing_trip(db, client, tt_env):
    status, _ = _get(client, "/api/v1/trip-tickets/999999", _token(client))
    assert status == 404


def test_list_shows_created_trips(db, client, tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    _post(client, "/api/v1/trip-tickets", _token(client),
          _payload(tt_env, driver_name_manual="X"))
    status, body = _get(client, "/api/v1/trip-tickets", _token(client))
    assert status == 200
    assert body["items"][0]["destination"] == "Carmona Plant"


# ── Lifecycle: release and complete ────────────────────────────────────────

def test_release_and_complete_are_routed(db, client, tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    _status, trip = _post(client, "/api/v1/trip-tickets", _token(client),
                          _payload(tt_env, driver_name_manual="X"))
    for action in ("submit", "release", "cancel"):
        status, _ = _post(client, f"/api/v1/trip-tickets/{trip['id']}/{action}",
                          _token(client))
        assert status != 404, f"/{action} is not routed"


def test_complete_records_the_return_and_advances_the_odometer(db, client,
                                                                tt_env):
    """TripTicketService.complete pushes odometer_in onto Vehicle
    Master's current odometer, but only when it moves FORWARD -- the
    same non-regressing guard Maintenance Order uses. A completed trip
    must never wind a vehicle's odometer backwards."""
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    _status, trip = _post(client, "/api/v1/trip-tickets", _token(client),
                          _payload(tt_env, driver_name_manual="X"))
    status, body = _post(
        client, f"/api/v1/trip-tickets/{trip['id']}/complete", _token(client),
        {"odometer_in": 5400,
         "return_datetime": datetime.now().replace(microsecond=0).isoformat()})
    assert status == 200, body
    assert body["odometer_in"] == 5400
    assert body["status"] == "COMPLETED"

    from app.modules.master_data.vehicle.models import Vehicle
    fresh = Vehicle.query.filter_by(id=tt_env["vehicle"].id).first()
    assert fresh.current_odometer == 5400


def test_complete_never_winds_the_vehicle_odometer_backwards(db, client,
                                                             tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    _status, trip = _post(client, "/api/v1/trip-tickets", _token(client),
                          _payload(tt_env, driver_name_manual="X"))
    _post(client, f"/api/v1/trip-tickets/{trip['id']}/complete", _token(client),
          {"odometer_in": 100,
           "return_datetime": datetime.now().replace(microsecond=0).isoformat()})

    from app.modules.master_data.vehicle.models import Vehicle
    fresh = Vehicle.query.filter_by(id=tt_env["vehicle"].id).first()
    assert fresh.current_odometer == 5000


def test_complete_requires_an_odometer_reading(db, client, tt_env):
    _set_param(db, "REQUIRE_DRIVER_FROM_MASTER", "NO")
    _status, trip = _post(client, "/api/v1/trip-tickets", _token(client),
                          _payload(tt_env, driver_name_manual="X"))
    status, _ = _post(client, f"/api/v1/trip-tickets/{trip['id']}/complete",
                      _token(client), {})
    assert status == 400
