"""Mobile Home for the assignee and "Tires & battery of my vehicle"
(approved mockups 2 and 3, 2026-10-15; built 2026-10-20).

GET /my/vehicles/<id>/coming-up -- the Home screen's "Coming up for
<plate>" list: every maintenance schedule on the unit BY NAME (Preventive
Maintenance Service, Aircon Servicing, Tire Rotation, ...) with what it
means today, plus the registration renewal. An order already raised for
a schedule shows as that order (number, who it is with) instead.

GET /my/vehicles/<id>/equipment -- the tires on each wheel (tread,
condition, km run) and the battery (installed, last test, warranty).

Both follow the /my/* rule: 404 for a vehicle you do not hold.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.driver.models import Driver
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.vehicle.assignment_service import (
    VehicleAssignmentService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.user_management.models import Permission, Role, User

TODAY = date.today()


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        module, action = code.split(".")
        p = Permission(code=code, module=module, action=action)
        db.session.add(p); db.session.flush()
    return p


@pytest.fixture()
def env(app):
    b = Branch(code="HOM", name="Muntinlupa")
    vt = VehicleType(code="LT-HOM", name="Light", category="LIGHT")
    db.session.add_all([b, vt]); db.session.commit()
    role = Role(name="r-hom", description="r")
    db.session.add(role); db.session.flush()
    role.permissions.append(_perm("vehicle.view"))
    u = User(username="hom_luis", email="hom@example.com",
             password_hash=hash_password("secret123"), is_active=True,
             branch_id=b.id, mobile_access=True)
    u.roles.append(role)
    db.session.add(u); db.session.commit()
    d = Driver(person_id="PID-HOM", employee_number="EMP-HOM",
               first_name="Luis", last_name="Bautista",
               assignee_type="DRIVER", branch_id=b.id)
    db.session.add(d); db.session.commit()
    DriverService().link_user(d.id, u.id)
    mine = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=b.id, conduction_number="HOM-A", plate_number="NZO-619",
        current_odometer=45320)
    theirs = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="DMax", year=2019,
        branch_id=b.id, conduction_number="HOM-B", plate_number="NAB-4471")
    db.session.commit()
    VehicleAssignmentService().assign(mine.id, d.id, source="MANUAL")
    return dict(mine=mine, theirs=theirs, branch=b)


def _get(client, path):
    r = client.post("/api/v1/auth/token",
                    json={"username": "hom_luis", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    r = client.get(f"/api/v1{path}",
                   headers={"Authorization": f"Bearer {tok}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


@pytest.fixture()
def due(monkeypatch):
    holder = {"items": []}
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    monkeypatch.setattr(PMDueCalculationService, "vehicle_due_items",
                        lambda self, vehicle, *a, **k: holder["items"])
    return holder


def _item(name, status, due_km=None, due_date=None, type_id=1, **kw):
    base = {"maintenance_type_id": type_id, "maintenance_type": name,
            "code": None, "equipment_kind": None, "status": status,
            "next_due_km": due_km, "next_due_date": due_date,
            "interval_km": None, "interval_days": None, "last_km": None,
            "last_date": None, "open_order": None}
    base.update(kw)
    return base


def _find(body, key):
    hits = [i for i in body["items"] if i["key"] == key]
    return hits[0] if hits else None


# ── coming up ────────────────────────────────────────────────────────

def test_coming_up_is_404_for_a_vehicle_you_do_not_hold(client, env, due):
    status, _ = _get(client, f"/my/vehicles/{env['theirs'].id}/coming-up")
    assert status == 404


def test_each_schedule_is_named_with_what_it_means_today(client, env, due):
    due["items"] = [
        _item("Tire Rotation", "OVERDUE", due_km=44900, type_id=2,
              interval_km=10000, last_km=34900),
        _item("Aircon Servicing", "GOOD", due_km=46000, type_id=4,
              interval_km=20000, last_km=26000)]
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    rot = _find(body, "pms-2")
    assert rot["title"] == "Tire Rotation"
    assert rot["state"] == "overdue"
    assert rot["badge"] == "Overdue"
    assert rot["detail"] == "Every 10,000 km · last at 34,900 km"
    ac = _find(body, "pms-4")
    assert ac["title"] == "Aircon Servicing"
    assert ac["state"] == "ok"
    assert ac["badge"] == "in 680 km"


def test_a_calendar_schedule_counts_days(client, env, due):
    due["items"] = [_item("Battery Check", "GOOD", type_id=3,
                          due_date=TODAY + timedelta(days=41),
                          interval_days=180)]
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    item = _find(body, "pms-3")
    assert item["badge"] == "in 41 days"
    assert item["detail"].startswith("Every 6 months")


def test_an_order_already_raised_shows_as_that_order(client, env, due):
    mo = MaintenanceOrder(document_number="MO-2026-000033",
                          vehicle_id=env["mine"].id,
                          order_category="MAINTENANCE", status="DRAFT",
                          scheduled_date=TODAY)
    db.session.add(mo); db.session.commit()
    due["items"] = [_item("Preventive Maintenance Service", "DUE_SOON",
                          due_km=45500, open_order={
                              "id": mo.id,
                              "document_number": "MO-2026-000033",
                              "status": "DRAFT"})]
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    item = _find(body, "pms-1")
    assert item["title"] == "Preventive Maintenance Service"
    assert item["detail"].startswith("MO-2026-000033")
    assert item["state"] == "awaiting"
    assert item["badge"] == "Awaiting"
    assert item["order_id"] == mo.id


def test_an_order_in_the_workshop_says_so(client, env, due):
    mo = MaintenanceOrder(document_number="MO-2026-000034",
                          vehicle_id=env["mine"].id,
                          order_category="MAINTENANCE", status="IN_PROGRESS",
                          scheduled_date=TODAY)
    db.session.add(mo); db.session.commit()
    due["items"] = [_item("Preventive Maintenance Service", "OVERDUE",
                          due_km=45000, open_order={
                              "id": mo.id,
                              "document_number": "MO-2026-000034",
                              "status": "IN_PROGRESS"})]
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    assert _find(body, "pms-1")["badge"] == "In the workshop"


def test_registration_renewal_is_listed(client, env, due):
    env["mine"].last_known_registration_expiry = date(TODAY.year + 1, 9, 5)
    db.session.commit()
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    reg = _find(body, "registration")
    assert reg["title"] == "Registration renewal"
    assert "plate ending 9" in reg["detail"]
    assert reg["badge"] == f"Sep {TODAY.year + 1}"


def test_next_pms_is_the_most_urgent_schedule(client, env, due):
    due["items"] = [
        _item("Preventive Maintenance Service", "DUE_SOON", due_km=46000,
              type_id=1),
        _item("Aircon Servicing", "GOOD", due_km=60000, type_id=4)]
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    assert body["next_pms"]["title"] == "Preventive Maintenance Service"
    assert body["next_pms"]["text"] == "in 680 km"


def test_nothing_scheduled_still_answers(client, env, due):
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/coming-up")
    assert body["next_pms"] is None
    assert all(not i["key"].startswith("pms") for i in body["items"])


# ── equipment ────────────────────────────────────────────────────────

def _tire(env, pos, serial, mm, mounted_km=None):
    t = Tire(serial_number=serial, brand="Bridgestone", model="Ecopia EP150",
             size="185/60 R15", tire_type="RADIAL", status="MOUNTED",
             vehicle_id=env["mine"].id, wheel_position=pos,
             current_tread_mm=mm, mounted_date=TODAY - timedelta(days=200),
             mounted_odometer=mounted_km, branch_id=env["branch"].id)
    db.session.add(t); db.session.commit()
    return t


def test_equipment_is_404_for_a_vehicle_you_do_not_hold(client, env):
    status, _ = _get(client, f"/my/vehicles/{env['theirs'].id}/equipment")
    assert status == 404


def test_tires_by_wheel_with_condition_and_km_run(client, env):
    _tire(env, "RR", "BS-24-11873", 3.4, mounted_km=14120)
    _tire(env, "FL", "BS-25-02210", 6.1, mounted_km=32920)
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/equipment")
    tires = {t["position"]: t for t in body["tires"]}
    assert tires["RR"]["condition"] == "WATCH"
    assert tires["RR"]["km_on_it"] == 31200
    assert tires["FL"]["condition"] == "GOOD"
    assert tires["FL"]["tread_mm"] == 6.1
    assert tires["FL"]["label"] == "Bridgestone Ecopia EP150"
    # Most urgent first, so the one to look at is at the top.
    assert body["tires"][0]["position"] == "RR"
    assert body["thresholds"]["tire_watch_mm"] == 4.0


def test_last_inspection_names_its_document(client, env):
    t = _tire(env, "FL", "BS-25-02210", 6.1)
    mo = MaintenanceOrder(document_number="MO-2026-000021",
                          vehicle_id=env["mine"].id,
                          order_category="MAINTENANCE", status="COMPLETED",
                          scheduled_date=TODAY)
    db.session.add(mo); db.session.flush()
    db.session.add(TireTransaction(
        document_number="TIR-HOM-1", tire_id=t.id,
        vehicle_id=env["mine"].id, action="INSPECT",
        transaction_date=date(2026, 9, 28), tread_depth_mm=6.1,
        status="COMPLETED", source="MO", maintenance_order_id=mo.id))
    db.session.commit()
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/equipment")
    assert body["last_inspection"] == {"date": "2026-09-28",
                                       "document": "MO-2026-000021"}


def test_battery_with_warranty_end_and_health(client, env):
    db.session.add(Battery(
        serial_number="MT-25-7781", brand="Motolite", model="Gold NS40ZL",
        voltage=12, capacity_ah=35, status="MOUNTED",
        vehicle_id=env["mine"].id, mounted_date=date(2025, 2, 10),
        warranty_months=24, last_voltage=12.6,
        last_test_date=TODAY - timedelta(days=20),
        branch_id=env["branch"].id))
    db.session.commit()
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/equipment")
    bat = body["battery"]
    assert bat["label"] == "Motolite Gold NS40ZL · 12 V · 35 Ah"
    assert bat["installed"] == "2025-02-10"
    assert bat["last_voltage"] == 12.6
    assert bat["warranty_to"] == "2027-02-10"
    assert bat["condition"] == "GOOD"


def test_no_equipment_registered_is_an_honest_empty_answer(client, env):
    _, body = _get(client, f"/my/vehicles/{env['mine'].id}/equipment")
    assert body["tires"] == []
    assert body["battery"] is None
    assert body["last_inspection"] is None
