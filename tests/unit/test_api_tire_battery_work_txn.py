"""API for the standalone tire / battery work document (2026-10-19)."""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.maintenance_config.service import PMScopeTemplateService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="tb-work")
    role.permissions = Permission.query.filter(Permission.code.in_([
        "tire.view", "tire.create", "tire.update", "battery.view",
        "battery.create", "battery.update"])).all()
    u = User(username="tbw", email="tbw@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    branch = BranchService().create(code="BR-TW", name="Muntinlupa")
    vt = VehicleTypeService().create(code="LV-TW", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=branch.id, conduction_number="TW-1", plate_number="NZO-619")
    vendor = VendorService().create(code="VEN-TW", name="Gulf Tire Center")
    fr = TireService().create(serial_number="BS-FR", brand="Bridgestone",
                              size="185/60 R15", tire_type="RADIAL")
    fr.status, fr.vehicle_id, fr.wheel_position = "MOUNTED", v.id, "FR"
    b = BatteryService().create(serial_number="MT-OLD", brand="Motolite")
    b.status, b.vehicle_id = "MOUNTED", v.id
    tire_mt = MaintenanceTypeService().create(
        code="PMS-TIRE", name="Tire Replacement", category="PM",
        equipment_kind="TIRE")
    cm = MaintenanceTypeService().create(code="CM", name="Repair",
                                         category="CM")
    PMScopeTemplateService().create(
        maintenance_type_id=tire_mt.id, name="Tire replacement",
        items=[{"activity_description": "Mount new tire, new valve"},
               {"activity_description": "Wheel balancing"}])
    for code, name in [("TIR", "Tire Transaction"),
                       ("BAT", "Battery Transaction")]:
        dt = DocumentTypeService().create(code=code, name=name,
                                          requires_approval=False,
                                          auto_numbering=True)
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    db.session.commit()
    mo = MaintenanceOrderService().create(
        vehicle_id=v.id, scheduled_date=date(2026, 10, 15), user=u,
        maintenance_type_id=cm.id)
    return dict(v=v, vendor=vendor, fr=fr, mo=mo, b=b)


def _h(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "tbw", "password": "secret123"})
    return {"Authorization": "Bearer " + r.get_json()["access_token"]}


REPLACE = {
    "action": "REPLACE", "wheel_position": "FR",
    "transaction_date": "2026-10-15", "odometer_at_service": 45410,
    "removal_reason": "SIDEWALL", "removed_to": "FOR_DISPOSAL",
    "install_source": "VENDOR", "new_serial": "BS-26-00415",
    "new_brand": "Bridgestone", "new_model": "Ecopia EP150",
    "new_size": "185/60 R15", "tread_depth_mm": "8.0", "pressure_psi": 33,
    "technician": "R. Dizon", "cost": "4,850.00", "labor_cost": "350",
    "scope_items": ["Mount new tire, new valve", "Wheel balancing"],
    "remarks": "Sidewall cut on SLEX",
}


def test_form_context(client, env):
    r = client.get(f"/api/v1/tire-transactions/form-context?vehicle_id={env['v'].id}",
                   headers=_h(client))
    assert r.status_code == 200
    b = r.get_json()
    assert [t["serial_number"] for t in b["installed"]["tires"]] == ["BS-FR"]
    assert b["installed"]["tires"][0]["wheel_position"] == "FR"
    assert [o["id"] for o in b["open_orders"]] == [env["mo"].id]
    assert b["scope_templates"][0]["name"] == "Tire replacement"
    assert b["scope_templates"][0]["items"] == [
        "Mount new tire, new valve", "Wheel balancing"]
    assert "REPLACE" in b["actions"]


def test_create_replace_document(client, env):
    body = dict(REPLACE, vehicle_id=env["v"].id, vendor_id=env["vendor"].id,
                maintenance_order_id=env["mo"].id)
    r = client.post("/api/v1/tire-transactions", headers=_h(client), json=body)
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()
    assert d["action"] == "REPLACE" and d["status"] == "COMPLETED"
    assert d["tire_serial"] == "BS-26-00415"
    assert d["removed_serial"] == "BS-FR"
    assert d["removed_to"] == "FOR_DISPOSAL"
    assert d["removal_reason"] == "SIDEWALL"
    assert d["vendor_name"] == "Gulf Tire Center"
    assert d["technician"] == "R. Dizon"
    assert (d["cost"], d["labor_cost"]) == (4850.0, 350.0)
    assert d["maintenance_order_number"] == env["mo"].document_number
    assert [i["activity_description"] for i in d["scope_items"]] == [
        "Mount new tire, new valve", "Wheel balancing"]
    new = Tire.query.filter_by(serial_number="BS-26-00415").one()
    assert (new.status, new.wheel_position) == ("MOUNTED", "FR")


def test_replace_validation_is_field_named(client, env):
    body = dict(REPLACE, vehicle_id=env["v"].id, new_brand="")
    r = client.post("/api/v1/tire-transactions", headers=_h(client), json=body)
    assert r.status_code == 400
    assert "new_brand" in r.get_json()["fields"]


def test_plain_actions_still_need_a_tire(client, env):
    r = client.post("/api/v1/tire-transactions", headers=_h(client), json={
        "action": "INSPECT", "transaction_date": "2026-10-15",
        "vehicle_id": env["v"].id})
    assert r.status_code == 400
    assert "tire_id" in r.get_json()["fields"]


def test_battery_replace_document(client, env):
    r = client.post("/api/v1/battery-transactions", headers=_h(client), json={
        "action": "REPLACE", "vehicle_id": env["v"].id,
        "transaction_date": "2026-10-15", "odometer_at_service": 45410,
        "removed_to": "FOR_DISPOSAL", "install_source": "VENDOR",
        "new_serial": "MT-NEW", "new_brand": "Motolite", "new_model": "NS40ZL",
        "new_warranty_months": 18, "voltage": "12.7", "test_result": "GOOD",
        "scope_items": ["Install and test charging"]})
    assert r.status_code == 201, r.get_data(as_text=True)
    d = r.get_json()
    assert d["battery_serial"] == "MT-NEW"
    assert d["removed_serial"] == "MT-OLD"
    assert d["odometer_at_service"] == 45410
    assert d["new_warranty_months"] == 18


def test_battery_form_context(client, env):
    r = client.get(f"/api/v1/battery-transactions/form-context?vehicle_id={env['v'].id}",
                   headers=_h(client))
    b = r.get_json()
    assert [x["serial_number"] for x in b["installed"]["batteries"]] == ["MT-OLD"]
    assert "TEST" in b["actions"] and "RETREAD" not in b["actions"]
