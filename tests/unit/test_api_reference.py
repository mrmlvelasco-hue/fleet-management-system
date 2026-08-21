"""Reference-data endpoints for JWT clients.

The Vehicle form's dropdowns are fed in Jinja by `api_search`, which is
gated by @login_required -- SESSION auth. React holds a bearer token and
cannot reach any of it, so its Brand, Model, Transmission and Fuel Type
inputs shipped as free text. Brand and model are still rejected on save
(the API passes strict=True), but transmission and fuel type have no
server-side membership check at all: free text there writes values the
Flask dropdown could never produce, and they surface in the Vehicle
Register report's own columns.

These endpoints are gated on `vehicle.view`, matching the reasoning
already recorded on /vehicle-types: reference data needed to FILL a form
must not require the owning module's management permission, or the
dropdown renders empty for exactly the people expected to use it.

Every one of them wraps an existing service. None re-implements a rule.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.system_admin.services.lookup_service import LookupService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def ref_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Vehicle Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    user = User(username="refuser", email="ref@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    # Holds no vehicle permission at all.
    nobody_role = Role(name="No Vehicle Access")
    nobody = User(username="nobody", email="nb@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    nobody.roles = [nobody_role]
    db.session.add_all([role, user, nobody_role, nobody])

    toyota = VehicleBrandService().create(name="Toyota")
    isuzu = VehicleBrandService().create(name="Isuzu")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux")
    VehicleModelService().create(brand_id=toyota.id, name="Vios")
    VehicleModelService().create(brand_id=isuzu.id, name="D-Max")

    LookupService().create(lookup_type="FUEL_TYPE", code="DIESEL",
                           description="Diesel", sort_order=1)
    LookupService().create(lookup_type="FUEL_TYPE", code="GAS",
                           description="Gasoline", sort_order=2)
    LookupService().create(lookup_type="TRANSMISSION", code="AT",
                           description="Automatic", sort_order=1)

    vt = VehicleTypeService().create(code="LV-REF", name="Light Vehicle",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-REF", name="Reference Branch")
    db.session.commit()
    return {"user": user, "toyota": toyota, "isuzu": isuzu,
            "vehicle_type": vt, "branch": branch}


def _token(client, username="refuser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _get(client, url, token):
    r = client.get(url, headers=_auth(token))
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Auth ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/api/v1/reference/vehicle-brands",
    "/api/v1/reference/vehicle-models?brand_id=1",
    "/api/v1/reference/lookups?types=FUEL_TYPE",
    "/api/v1/reference/pm-schedules",
    "/api/v1/reference/drivers",
    "/api/v1/reference/departments",
])
def test_reference_endpoints_reject_anonymous(db, client, ref_env, path):
    """Reference data is still fleet data. Brand lists are dull; the
    driver roster is a staff list, and the endpoint must not be open
    merely because a dropdown consumes it."""
    assert client.get(path).status_code == 401


def test_reference_endpoints_require_vehicle_view(db, client, ref_env):
    t = _token(client, "nobody")
    assert client.get("/api/v1/reference/vehicle-brands",
                      headers=_auth(t)).status_code == 403


# ── Brands and models ───────────────────────────────────────────────────────

def test_brands_returns_the_master_list(db, client, ref_env):
    status, body = _get(client, "/api/v1/reference/vehicle-brands",
                        _token(client))
    assert status == 200
    assert {b["name"] for b in body["items"]} == {"Toyota", "Isuzu"}


def test_models_are_filtered_to_the_requested_brand(db, client, ref_env):
    """The whole point of the cascade. Returning every model would offer
    a Toyota Vios under Isuzu, and strict=True would then reject the save
    with an error the user could not have predicted from the dropdown."""
    status, body = _get(
        client,
        f"/api/v1/reference/vehicle-models?brand_id={ref_env['toyota'].id}",
        _token(client))
    assert status == 200
    assert {m["name"] for m in body["items"]} == {"Hilux", "Vios"}


def test_models_without_a_brand_returns_empty_not_everything(db, client,
                                                            ref_env):
    """Mirrors api_search: no brand_id yields no options, matching the
    form's '- Select a Brand first -' state. Falling back to the full
    list would make that state impossible to render."""
    status, body = _get(client, "/api/v1/reference/vehicle-models",
                        _token(client))
    assert status == 200
    assert body["items"] == []


def test_models_for_a_brand_with_none_is_distinguishable(db, client, ref_env):
    """Flask shows '- No models yet for this Brand -', a different fact
    from 'no brand chosen'. The response has to let the client tell them
    apart, so brand_id is echoed back."""
    solo = VehicleBrandService().create(name="Foton")
    status, body = _get(
        client, f"/api/v1/reference/vehicle-models?brand_id={solo.id}",
        _token(client))
    assert status == 200
    assert body["items"] == []
    assert body["brand_id"] == solo.id


def test_models_rejects_a_non_integer_brand(db, client, ref_env):
    status, _ = _get(client, "/api/v1/reference/vehicle-models?brand_id=abc",
                     _token(client))
    assert status == 400


# ── Lookups ─────────────────────────────────────────────────────────────────

def test_lookups_returns_requested_types_keyed_by_type(db, client, ref_env):
    """One request for several types. The form needs four lists at once
    and four round-trips would render the dropdowns at four different
    moments."""
    status, body = _get(
        client, "/api/v1/reference/lookups?types=FUEL_TYPE,TRANSMISSION",
        _token(client))
    assert status == 200
    assert {i["code"] for i in body["items"]["FUEL_TYPE"]} == {"DIESEL", "GAS"}
    assert body["items"]["TRANSMISSION"][0]["description"] == "Automatic"


def test_lookups_preserves_sort_order(db, client, ref_env):
    """get_by_type orders by sort_order then code. A dropdown that
    reorders itself between the two apps looks like different data."""
    _status, body = _get(client, "/api/v1/reference/lookups?types=FUEL_TYPE",
                         _token(client))
    assert [i["code"] for i in body["items"]["FUEL_TYPE"]] == ["DIESEL", "GAS"]


def test_lookups_uses_the_registry_fallback(db, client, ref_env):
    """COMPONENT_GROUP is unseeded here. Flask calls
    get_by_type_with_fallback for it precisely so the dropdown is not
    empty on a fresh install; an empty list would read as 'this fleet has
    no component groups'."""
    _status, body = _get(client,
                         "/api/v1/reference/lookups?types=COMPONENT_GROUP",
                         _token(client))
    assert body["items"]["COMPONENT_GROUP"], (
        "COMPONENT_GROUP fell back to nothing")


def test_lookups_rejects_an_unknown_type(db, client, ref_env):
    """A typo must not return {} and let the form render a silently empty
    dropdown that looks like unseeded master data."""
    status, _ = _get(client, "/api/v1/reference/lookups?types=NOT_A_TYPE",
                     _token(client))
    assert status == 400


def test_lookups_requires_a_types_parameter(db, client, ref_env):
    status, _ = _get(client, "/api/v1/reference/lookups", _token(client))
    assert status == 400


# ── PM schedules ────────────────────────────────────────────────────────────

def _pm_schedule(db, **kwargs):
    from app.modules.maintenance_config.models import PMSchedule
    mt = MaintenanceTypeService().create(
        code=kwargs.pop("mt_code", "PM-REF"), name="Preventive",
        category="PREVENTIVE")
    row = PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                     interval_km=5000, is_active=True, **kwargs)
    db.session.add(row)
    db.session.commit()
    return row


def test_pm_schedules_without_criteria_returns_nothing(db, client, ref_env):
    """Mirrors list_applicable_for_criteria's own early return. Listing
    every active template regardless of relevance is the bug Flask
    already fixed once on this dropdown."""
    status, body = _get(client, "/api/v1/reference/pm-schedules",
                        _token(client))
    assert status == 200
    assert body["items"] == []


def test_pm_schedules_match_on_brand_and_model(db, client, ref_env):
    model = VehicleModelService().list(brand_id=ref_env["toyota"].id)[0]
    row = _pm_schedule(db, vehicle_brand_id=ref_env["toyota"].id,
                       vehicle_model_id=model.id)
    _status, body = _get(
        client,
        f"/api/v1/reference/pm-schedules?brand_name=Toyota"
        f"&model_name={model.name}",
        _token(client))
    assert [i["id"] for i in body["items"]] == [row.id]


def test_pm_schedules_fall_back_to_vehicle_type(db, client, ref_env):
    row = _pm_schedule(db, vehicle_type_id=ref_env["vehicle_type"].id)
    _status, body = _get(
        client,
        "/api/v1/reference/pm-schedules?vehicle_type_id="
        f"{ref_env['vehicle_type'].id}",
        _token(client))
    assert row.id in [i["id"] for i in body["items"]]


def test_current_assignment_survives_a_criteria_change(db, client, ref_env):
    """vehicle_edit re-inserts the assigned template when the criteria
    filter would exclude it (routes.py 1170-1176). Without that, a
    deliberate manual override vanishes from its own dropdown and the
    next save silently clears it -- the field looks blank because it IS
    blank, and nothing reports the loss.
    """
    orphan = _pm_schedule(db, mt_code="PM-ORPH",
                          vehicle_type_id=ref_env["vehicle_type"].id)
    # Criteria that cannot match it.
    _status, body = _get(
        client,
        f"/api/v1/reference/pm-schedules?brand_name=Isuzu&model_name=D-Max"
        f"&current_id={orphan.id}",
        _token(client))
    ids = [i["id"] for i in body["items"]]
    assert orphan.id in ids
    assert body["items"][0]["id"] == orphan.id, (
        "the pinned assignment should lead the list, as in Flask")


def test_pm_schedule_labels_carry_the_interval(db, client, ref_env):
    """Several packages can exist for one vehicle (imported profiles).
    Without the interval in the label they are indistinguishable, which
    the Flask template calls out directly."""
    model = VehicleModelService().list(brand_id=ref_env["toyota"].id)[0]
    _pm_schedule(db, vehicle_brand_id=ref_env["toyota"].id,
                 vehicle_model_id=model.id)
    _status, body = _get(
        client,
        f"/api/v1/reference/pm-schedules?brand_name=Toyota"
        f"&model_name={model.name}", _token(client))
    assert "5,000 km" in body["items"][0]["label"]


# ── Drivers and departments ─────────────────────────────────────────────────

def _driver(db, employee_number, first, last, branch_id=None):
    """Built from the model directly, as test_driver_photo.py does.

    DriverService.create() requires a photo upload and full license
    details. Both are real rules and worth having, but neither is what
    these tests are about, and going through it here would make a
    dropdown test depend on the attachment service.
    """
    from app.modules.master_data.driver.models import Driver
    row = Driver(person_id=f"PID-REF-{employee_number}",
                 employee_number=employee_number, first_name=first,
                 last_name=last, assignee_type="DRIVER",
                 license_type="PROFESSIONAL", branch_id=branch_id,
                 is_active=True)
    db.session.add(row)
    db.session.commit()
    return row


def test_drivers_are_searchable_by_number_and_name(db, client, ref_env):
    _driver(db, "EMP-9001", "Juan", "Dela Cruz",
            branch_id=ref_env["branch"].id)
    _driver(db, "EMP-9002", "Maria", "Santos", branch_id=ref_env["branch"].id)
    db.session.commit()

    _status, by_name = _get(client, "/api/v1/reference/drivers?q=Santos",
                            _token(client))
    assert [d["employee_number"] for d in by_name["items"]] == ["EMP-9002"]

    _status, by_number = _get(client, "/api/v1/reference/drivers?q=9001",
                              _token(client))
    assert [d["employee_number"] for d in by_number["items"]] == ["EMP-9001"]


def test_driver_label_matches_the_flask_option(db, client, ref_env):
    """'{employee_number} - {last}, {first} ({license_type})'. The
    selected option is what the user reads back to confirm they picked
    the right person out of a roster with repeated surnames."""
    _driver(db, "EMP-9003", "Jose", "Rizal", branch_id=ref_env["branch"].id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/drivers?q=Rizal",
                         _token(client))
    assert body["items"][0]["label"] == (
        "EMP-9003 — Rizal, Jose (PROFESSIONAL)")


def test_inactive_drivers_are_not_selectable(db, client, ref_env):
    """The form says 'only active drivers are selectable'. Offering a
    deactivated driver would assign accountability to someone who has
    left."""
    from app.modules.master_data.driver.service import DriverService
    gone = _driver(db, "EMP-9004", "Ex", "Employee",
                   branch_id=ref_env["branch"].id)
    db.session.commit()
    DriverService().deactivate(gone.id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/drivers?q=Employee",
                         _token(client))
    assert body["items"] == []


def test_departments_are_listed(db, client, ref_env):
    from app.modules.master_data.org.service import DepartmentService
    DepartmentService().create(code="OPS", name="Operations",
                               branch_id=ref_env["branch"].id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/reference/departments",
                         _token(client))
    assert "Operations" in [d["name"] for d in body["items"]]


def test_drivers_respect_the_callers_org_scope(db, client, ref_env):
    """A branch-scoped user must not see another branch's roster.

    Added after a mutation check: removing `user=api_user` from the
    DriverService call left every driver test passing. The endpoint is a
    dropdown feed, but the data behind it is a staff list, and scoping
    that had no coverage at all is scoping nobody would notice losing.
    """
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    other = BranchService().create(code="BR-OTHER", name="Other Branch")
    db.session.commit()

    _driver(db, "EMP-8001", "Mine", "Ours", branch_id=ref_env["branch"].id)
    _driver(db, "EMP-8002", "Theirs", "Elsewhere", branch_id=other.id)

    UserOrgScopeService().assign(ref_env["user"].id, scope_type="BRANCH",
                                 branch_id=ref_env["branch"].id)
    db.session.commit()

    _status, body = _get(client, "/api/v1/reference/drivers", _token(client))
    numbers = [d["employee_number"] for d in body["items"]]
    assert "EMP-8001" in numbers
    assert "EMP-8002" not in numbers
