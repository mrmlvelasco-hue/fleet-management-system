"""New MO form -- which PM package to recommend when a vehicle has
several PM profiles (Tire Replacement, PMS, Aircon, Battery...).

Reported: TIL-120 (odometer 0) had its Tire Replacement package
auto-selected although it is not due until 45,000 km / 2028. Cause:
with no Maintenance Type chosen, recommend() looks only at the FIRST
schedule the database returns (schedules[0], no ORDER BY) -- in practice
whichever profile was created first -- not the most urgent one.

new-prefill now compares every profile that applies to the vehicle and
returns the most urgent: OVERDUE, then DUE, then UPCOMING, then GOOD;
ties go to the earliest due point. recommend() itself is unchanged (it
is shared by other screens); an explicitly chosen Maintenance Type is
still honoured as before.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.maintenance_config.models import PMScopeItem, PMScopeTemplate
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _template_for(schedule, mt, name):
    t = PMScopeTemplate(maintenance_type_id=mt.id, name=name,
                        pm_schedule_id=schedule.id)
    t.items = [PMScopeItem(activity_code="X", activity_description=name,
                           sort_order=1)]
    _db.session.add(t)
    _db.session.commit()
    return t


@pytest.fixture()
def env(db):
    sync_permissions()
    vt = VehicleTypeService().create(code="LV-MU", name="Light",
                                     category="LIGHT")
    tire = MaintenanceTypeService().create(code="MU-TIRE", name="Tire",
                                           category="PM")
    pms = MaintenanceTypeService().create(code="MU-PMS", name="PMS",
                                          category="PM")
    # Created FIRST -> what schedules[0] used to pick. Not due for ages.
    tire_pkg = PMScheduleService().create(
        maintenance_type_id=tire.id, trigger_mode="KM", interval_km=45000,
        vehicle_type_id=vt.id, profile_code="MU-TIRE-P", sequence_position=1)
    # Created SECOND. Due now (within its notify window).
    pms_pkg = PMScheduleService().create(
        maintenance_type_id=pms.id, trigger_mode="KM", interval_km=1000,
        vehicle_type_id=vt.id, profile_code="MU-PMS-P", sequence_position=1)
    tire_t = _template_for(tire_pkg, tire, "Tire package")
    pms_t = _template_for(pms_pkg, pms, "PMS package")
    branch = BranchService().create(code="BR-MU", name="Most Urgent Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="MU-001")
    vehicle.current_odometer = 950          # PMS due at 1,000 km
    role = Role(name="mu-role")
    role.permissions = Permission.query.all()
    user = User(username="muuser", email="mu@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"vehicle": vehicle, "tire": tire, "pms": pms,
            "tire_t": tire_t, "pms_t": pms_t,
            "tire_pkg": tire_pkg, "pms_pkg": pms_pkg}


def _prefill(client, env, mt_id=None):
    r = client.post("/api/v1/auth/token",
                    json={"username": "muuser", "password": "secret123"})
    hdr = {"Authorization":
           f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}
    url = f"/api/v1/maintenance-orders/new-prefill?vehicle_id={env['vehicle'].id}"
    if mt_id:
        url += f"&maintenance_type_id={mt_id}"
    r = client.get(url, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["pm_recommendation"]


def test_no_type_chosen_recommends_the_most_urgent_profile(client, db, env):
    rec = _prefill(client, env)
    assert rec["status"] == "DUE"
    assert rec["scope_template_id"] == env["pms_t"].id   # not the tire one


def test_an_explicit_maintenance_type_is_still_honoured(client, db, env):
    rec = _prefill(client, env, env["tire"].id)
    assert rec["scope_template_id"] == env["tire_t"].id
    assert rec["status"] == "UPCOMING"                   # 45,000 km away


def test_nothing_due_reports_the_nearest_upcoming_package(client, db, env):
    """Odometer 0: PMS due at 1,000 km is outside its window too, so both
    are UPCOMING -- the nearer due point (PMS) is reported, still as
    UPCOMING, which the form must NOT auto-select."""
    env["vehicle"].current_odometer = 0
    _db.session.commit()
    rec = _prefill(client, env)
    assert rec["status"] == "UPCOMING"
    assert rec["scope_template_id"] == env["pms_t"].id


def test_status_beats_an_earlier_looking_date(client, db, env):
    """A calendar profile always has a due DATE; a km-only profile never
    does. The not-yet-due calendar package must not win over a DUE km
    package just because it is the only one with a date to compare."""
    aircon = MaintenanceTypeService().create(code="MU-AC", name="Aircon",
                                             category="PM")
    vt_id = env["vehicle"].vehicle_type_id
    ac_pkg = PMScheduleService().create(
        maintenance_type_id=aircon.id, trigger_mode="CALENDAR",
        interval_days=365, vehicle_type_id=vt_id, profile_code="MU-AC-P",
        sequence_position=1)
    _template_for(ac_pkg, aircon, "Aircon package")
    rec = _prefill(client, env)
    assert rec["status"] == "DUE"
    assert rec["scope_template_id"] == env["pms_t"].id



# ── the vehicle's own PM status (pm-status endpoint, odometer response) ──

def test_vehicle_pm_status_reports_the_most_urgent_package(client, db, env):
    """GET /vehicles/<id>/pm-status (and the pm_status returned after an
    odometer reading) used recommend(vehicle) -> schedules[0], i.e. the
    first profile in database order. It must report what is actually most
    urgent."""
    r = client.post("/api/v1/auth/token",
                    json={"username": "muuser", "password": "secret123"})
    hdr = {"Authorization":
           f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}
    r = client.get(f"/api/v1/vehicles/{env['vehicle'].id}/pm-status",
                   headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["status"] == "DUE"
    assert body["recommended_package_id"] == env["pms_pkg"].id
