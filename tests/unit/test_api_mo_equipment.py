"""API for the MO's Tire & Battery Work (2026-10-15)."""
import json
from datetime import date
from decimal import Decimal

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.user_management.models import Permission, Role, User


def _user(db, username, codes):
    role = Role(name=f"role-{username}")
    role.permissions = Permission.query.filter(
        Permission.code.in_(codes)).all()
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    db.session.commit()
    return u


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    editor = _user(db, "eq_editor", [
        "maintenanceorder.view", "maintenanceorder.create",
        "maintenanceorder.update", "maintenancetype.view",
        "maintenancetype.update", "maintenancetype.create"])
    viewer = _user(db, "eq_viewer", ["maintenanceorder.view"])
    branch = BranchService().create(code="BR-EA", name="Muntinlupa")
    vt = VehicleTypeService().create(code="LV-EA", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=branch.id, conduction_number="EA-001",
        plate_number="TIL-120")
    vendor = VendorService().create(code="VEN-EA", name="Gulf Tire Center")
    mt = MaintenanceTypeService().create(
        code="PMS-TIRE", name="Tire Replacement", category="PREVENTIVE",
        equipment_kind="TIRE")
    fl = TireService().create(serial_number="BS-FL", brand="Bridgestone",
                              size="185/60 R15", tire_type="RADIAL")
    fl.status, fl.vehicle_id, fl.wheel_position = "MOUNTED", vehicle.id, "FL"
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    db.session.commit()
    return dict(editor=editor, viewer=viewer, vehicle=vehicle, vendor=vendor,
                mt=mt, fl=fl)


def _tok(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization":
            "Bearer " + json.loads(r.get_data(as_text=True))["access_token"]}


def _body(r):
    return json.loads(r.get_data(as_text=True))


def _new_mo(env, lines=None):
    return MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, scheduled_date=date(2026, 10, 15),
        user=env["editor"], maintenance_type_id=env["mt"].id,
        vendor_id=env["vendor"].id, odometer_at_service=27000,
        equipment_lines=lines)


FL_DONE = {"kind": "TIRE", "position": "FL", "action": "REPLACE",
           "status": "DONE", "install_source": "VENDOR",
           "new_serial": "DOT-NEW-FL", "new_brand": "Bridgestone",
           "new_size": "185/60 R15", "tread_depth_mm": 8}


def test_prefill_endpoint(client, env):
    r = client.get(
        "/api/v1/maintenance-orders/equipment-prefill"
        f"?vehicle_id={env['vehicle'].id}&maintenance_type_id={env['mt'].id}",
        headers=_tok(client, "eq_editor"))
    assert r.status_code == 200
    b = _body(r)
    assert b["kind"] == "TIRE"
    assert b["lines"][0]["position"] == "FL"
    assert b["lines"][0]["current_tire_id"] == env["fl"].id
    assert b["thresholds"]["tire_watch_mm"] == 4.0
    assert b["installed"]["tires"][0]["serial_number"] == "BS-FL"


def test_prefill_needs_vehicle(client, env):
    r = client.get("/api/v1/maintenance-orders/equipment-prefill",
                   headers=_tok(client, "eq_editor"))
    assert r.status_code == 400


def test_create_mo_with_equipment_lines(client, env):
    r = client.post("/api/v1/maintenance-orders", headers=_tok(
        client, "eq_editor"), json={
        "vehicle_id": env["vehicle"].id, "scheduled_date": "2026-10-15",
        "maintenance_type_id": env["mt"].id,
        "equipment_lines": [{"kind": "TIRE", "position": "FL",
                             "action": "REPLACE",
                             "current_tire_id": env["fl"].id}]})
    assert r.status_code == 201, r.get_data(as_text=True)
    b = _body(r)
    assert b["equipment_kind"] == "TIRE"
    assert [(l["position"], l["status"], l["current_label"])
            for l in b["equipment_lines"]] == [
        ("FL", "PLANNED", "BS-FL · Bridgestone · 185/60 R15")]


def test_create_mo_with_bad_line_is_a_400_naming_the_field(client, env):
    r = client.post("/api/v1/maintenance-orders", headers=_tok(
        client, "eq_editor"), json={
        "vehicle_id": env["vehicle"].id, "scheduled_date": "2026-10-15",
        "maintenance_type_id": env["mt"].id,
        "equipment_lines": [{"kind": "TIRE", "action": "REPLACE"}]})
    assert r.status_code == 400
    b = _body(r)
    assert b["fields"] == {"equipment_lines": b["message"]} or \
        "position" in json.dumps(b)
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    assert MaintenanceOrder.query.count() == 0


def test_detail_includes_equipment_lines(client, env):
    mo = _new_mo(env, [dict(FL_DONE, current_tire_id=env["fl"].id)])
    r = client.get(f"/api/v1/maintenance-orders/{mo.id}",
                   headers=_tok(client, "eq_viewer"))
    b = _body(r)
    assert b["equipment_kind"] == "TIRE"
    assert b["equipment_lines"][0]["new_serial"] == "DOT-NEW-FL"
    assert b["equipment_lines"][0]["status"] == "DONE"


def test_put_lines_by_editor(client, env):
    mo = _new_mo(env)
    r = client.put(f"/api/v1/maintenance-orders/{mo.id}/equipment-lines",
                   headers=_tok(client, "eq_editor"),
                   json={"lines": [dict(FL_DONE,
                                        current_tire_id=env["fl"].id)]})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _body(r)["equipment_lines"][0]["status"] == "DONE"


def test_put_lines_refused_for_view_only_user(client, env):
    mo = _new_mo(env)
    r = client.put(f"/api/v1/maintenance-orders/{mo.id}/equipment-lines",
                   headers=_tok(client, "eq_viewer"), json={"lines": []})
    assert r.status_code == 403


def test_put_lines_validation_names_field_and_line(client, env):
    mo = _new_mo(env)
    r = client.put(f"/api/v1/maintenance-orders/{mo.id}/equipment-lines",
                   headers=_tok(client, "eq_editor"),
                   json={"lines": [dict(FL_DONE, new_brand="",
                                        current_tire_id=env["fl"].id)]})
    assert r.status_code == 400
    b = _body(r)
    assert b["field"] == "new_brand"
    assert b["line"] == 0


def test_put_lines_on_completed_order_is_a_409(client, env):
    mo = _new_mo(env)
    MaintenanceOrderService().complete(mo.id, 0, date(2026, 10, 16))
    r = client.put(f"/api/v1/maintenance-orders/{mo.id}/equipment-lines",
                   headers=_tok(client, "eq_editor"), json={"lines": []})
    assert r.status_code == 409


def test_complete_endpoint_posts_transactions(client, env):
    mo = _new_mo(env, [dict(FL_DONE, current_tire_id=env["fl"].id)])
    r = client.post(f"/api/v1/maintenance-orders/{mo.id}/complete",
                    headers=_tok(client, "eq_editor"),
                    json={"completed_date": "2026-10-16"})
    assert r.status_code == 200, r.get_data(as_text=True)
    txns = TireTransaction.query.all()
    assert {t.action for t in txns} == {"DISMOUNT", "MOUNT"}
    assert all(t.requested_by == env["editor"].id for t in txns)
    assert Tire.query.filter_by(serial_number="DOT-NEW-FL").one().status == \
        "MOUNTED"


def test_complete_endpoint_409_while_planned(client, env):
    mo = _new_mo(env, [{"kind": "TIRE", "position": "FL",
                        "action": "REPLACE",
                        "current_tire_id": env["fl"].id}])
    r = client.post(f"/api/v1/maintenance-orders/{mo.id}/complete",
                    headers=_tok(client, "eq_editor"), json={})
    assert r.status_code == 409
    assert "Planned" in _body(r)["message"]


def test_maintenance_type_api_exposes_equipment_kind(client, env):
    h = _tok(client, "eq_editor")
    r = client.get(f"/api/v1/maintenance-types/{env['mt'].id}", headers=h)
    assert _body(r)["equipment_kind"] == "TIRE"
    r = client.put(f"/api/v1/maintenance-types/{env['mt'].id}", headers=h,
                   json={"equipment_kind": "BATTERY"})
    assert _body(r)["equipment_kind"] == "BATTERY"
    r = client.put(f"/api/v1/maintenance-types/{env['mt'].id}", headers=h,
                   json={"equipment_kind": ""})
    assert _body(r)["equipment_kind"] is None
    r = client.put(f"/api/v1/maintenance-types/{env['mt'].id}", headers=h,
                   json={"equipment_kind": "WHEEL"})
    assert r.status_code == 400


def test_tire_txn_list_shows_mo_link_and_position(client, env):
    """The Tire Transactions module: search by vehicle shows MO-sourced
    rows with their order number and wheel."""
    mo = _new_mo(env, [dict(FL_DONE, current_tire_id=env["fl"].id)])
    MaintenanceOrderService().complete(mo.id, 0, date(2026, 10, 16))
    _user_codes = ["tire.view"]
    _user(__import__("app.extensions", fromlist=["db"]).db, "tv",
          _user_codes)
    r = client.get("/api/v1/tire-transactions?q=TIL-120",
                   headers=_tok(client, "tv"))
    assert r.status_code == 200, r.get_data(as_text=True)
    items = _body(r)["items"]
    mount = next(i for i in items if i["action"] == "MOUNT")
    assert mount["source"] == "MO"
    assert mount["maintenance_order_id"] == mo.id
    assert mo.document_number and mo.document_number.startswith("MO-")
    assert mount["maintenance_order_number"] == mo.document_number
    assert mount["wheel_position"] == "FL"
