"""New MO form -- the vehicle's due PM package must not be offered for an
order that is not a PM order.

Reported: Transaction Type "Repainting" + a vehicle with a due Tire
Replacement PM schedule auto-selected that PM package and pre-filled its
Work Description and Scope of Work.

Two server-side pieces, tested here:
  1. GET /mo-transaction-types carries `maintenance_class`
     (PREVENTIVE / CORRECTIVE / PREDICTIVE) so the form can tell a
     Repainting order from a Servicing one. It was only on the admin
     endpoint, so the form never knew.
  2. GET /maintenance-orders/new-prefill answers "N/A" with no scope
     template when the supplied Maintenance Type is not PM-category.
"""
import json

import pytest

from app.cli import _seed_maintenance_classes, _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    _seed_maintenance_classes()
    db.session.commit()
    branch = BranchService().create(code="BR-PG", name="Prefill Guard Branch")
    vt = VehicleTypeService().create(code="LV-PG", name="Light",
                                     category="LIGHT")
    cm = MaintenanceTypeService().create(code="PG-CM", name="Normal Work",
                                         category="CM")
    pm = MaintenanceTypeService().create(code="PG-PM", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="PG-001")
    role = Role(name="pg-role")
    role.permissions = Permission.query.all()
    user = User(username="pguser", email="pg@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"vehicle": vehicle, "cm": cm, "pm": pm}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "pguser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ── 1. dropdown carries maintenance_class ───────────────────────────────

def test_transaction_type_dropdown_carries_maintenance_class(client, db, env):
    r = client.get("/api/v1/mo-transaction-types", headers=_hdr(client))
    assert r.status_code == 200
    by_code = {t["code"]: t for t in r.get_json()["items"]}
    assert by_code["MAINT-REPAINT"]["maintenance_class"] == "CORRECTIVE"
    assert by_code["MAINT-SERVICING"]["maintenance_class"] == "PREVENTIVE"
    assert by_code["MAINT-INSPECTION"]["maintenance_class"] == "PREDICTIVE"


def test_operational_types_have_no_maintenance_class(client, db, env):
    r = client.get("/api/v1/mo-transaction-types", headers=_hdr(client))
    by_code = {t["code"]: t for t in r.get_json()["items"]}
    assert by_code["ADM-CHG-COLOR"]["maintenance_class"] is None


# ── 2. new-prefill is N/A for a non-PM Maintenance Type ─────────────────

def test_new_prefill_is_not_applicable_for_a_non_pm_type(client, db, env):
    r = client.get(
        "/api/v1/maintenance-orders/new-prefill"
        f"?vehicle_id={env['vehicle'].id}&maintenance_type_id={env['cm'].id}",
        headers=_hdr(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    rec = r.get_json()["pm_recommendation"]
    assert rec["status"] == "N/A"
    assert rec["scope_template_id"] is None


def test_new_prefill_still_runs_for_a_pm_type(client, db, env):
    r = client.get(
        "/api/v1/maintenance-orders/new-prefill"
        f"?vehicle_id={env['vehicle'].id}&maintenance_type_id={env['pm'].id}",
        headers=_hdr(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    # No PM schedule exists for this vehicle, so there is nothing due --
    # but it must have gone through the real recommendation service
    # rather than the "not a PM type" shortcut.
    assert r.get_json()["pm_recommendation"]["status"] != "N/A"


def test_new_prefill_without_a_type_still_runs_the_recommendation(
        client, db, env):
    """No Maintenance Type yet (vehicle picked first): the endpoint must
    not guess -- the client decides from the Transaction Type."""
    r = client.get(
        f"/api/v1/maintenance-orders/new-prefill?vehicle_id={env['vehicle'].id}",
        headers=_hdr(client))
    assert r.status_code == 200
    assert r.get_json()["pm_recommendation"]["status"] != "N/A"
