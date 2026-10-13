"""A disposal (and a transfer) must be readable off the vehicle's own
history, naming the Maintenance Order that caused it (2026-10-13).

Michael: "for the disposal that need to be part of the vehicle history
also the MO for disposal request already part for the list to identify
which MO originate the disposal."

A disposal is raised as an Operational Maintenance Order whose
Transaction Type is in the DISPOSAL group; completing it retires the
vehicle and stamps an ADR number (a transfer stamps an ATR and moves the
branch). Both orders were already listed in the vehicle's maintenance
history, but with nothing on the row to say what they were: `category`
and PM type are empty for an operational order, so the retirement of the
asset read as a blank line. These tests pin the two places a person
looks -- the Maintenance History list and the Activity timeline -- to
the ADR/ATR number, what was disposed of and for how much, and the
originating order's own document number.
"""
import json
from datetime import date

import pytest

from app.cli import _seed_atr_numbering, _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.core.vehicle_activity_history_service import (
    VehicleActivityHistoryService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    _seed_atr_numbering()
    db.session.commit()
    origin = BranchService().create(code="BR-DSP1", name="Origin Branch")
    destination = BranchService().create(code="BR-DSP2", name="Cebu Branch")
    vt = VehicleTypeService().create(code="LV-DSP", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Mitsubishi", model="Strada", year=2013,
        branch_id=origin.id, conduction_number="DSP-001",
        plate_number="DSP-1234", acquisition_date=date(2013, 5, 15),
        acquisition_cost=950000)
    role = Role(name="DSP viewers", permissions=Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all())
    db.session.add(role)
    db.session.commit()
    user = User(username="dsp_viewer", email="dsp@e.com", first_name="Dee",
                last_name="Viewer", password_hash=hash_password("secret123"),
                is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()
    return dict(vehicle=vehicle, origin=origin, destination=destination,
                user=user)


def _disposal(env, *, value=250000, recipient="Manila Scrap Traders"):
    tt = TransactionType.query.filter_by(code="DIS-SOLD").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=env["vehicle"].id, order_category="OPERATIONAL",
                    transaction_type_id=tt.id,
                    description="Unit sold at auction",
                    scheduled_date=date(2026, 9, 1), user=env["user"])
    mo.disposal_value = value
    mo.disposal_recipient = recipient
    from app.extensions import db
    db.session.commit()
    return svc.complete(mo.id, actual_cost=0,
                        completed_date=date(2026, 9, 15))


def _transfer(env):
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=env["vehicle"].id, order_category="OPERATIONAL",
                    transaction_type_id=tt.id, description="Moved to Cebu",
                    scheduled_date=date(2026, 8, 1), user=env["user"],
                    destination_branch_id=env["destination"].id)
    return svc.complete(mo.id, actual_cost=0,
                        completed_date=date(2026, 8, 10))


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "dsp_viewer", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _history(client, env):
    r = client.get(f"/api/v1/vehicles/{env['vehicle'].id}/maintenance-history",
                   headers=_hdr(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["items"]


# ── the Maintenance History list (Vehicle detail) ───────────────────

def test_disposal_order_is_identifiable_in_the_vehicle_history(client, db, env):
    mo = _disposal(env)
    row = next(r for r in _history(client, env) if r["id"] == mo.id)
    assert row["document_number"] == mo.document_number
    assert row["activity_type"] == "Disposal"
    assert row["transaction_type"] == "Sold / Auctioned"
    assert row["reference_number"] == mo.disposal_reference_number
    assert row["reference_label"] == "ADR No."


def test_disposal_row_carries_the_value_and_recipient(client, db, env):
    _disposal(env, value=250000, recipient="Manila Scrap Traders")
    row = next(r for r in _history(client, env)
               if r["activity_type"] == "Disposal")
    assert row["disposal_value"] == "250000.00"
    assert row["disposal_recipient"] == "Manila Scrap Traders"


def test_transfer_row_names_the_branches_and_its_atr(client, db, env):
    mo = _transfer(env)
    row = next(r for r in _history(client, env) if r["id"] == mo.id)
    assert row["activity_type"] == "Transfer"
    assert row["reference_number"] == mo.transfer_reference_number
    assert row["reference_label"] == "ATR No."
    assert row["origin_branch"] == "Origin Branch"
    assert row["destination_branch"] == "Cebu Branch"


def test_a_maintenance_order_still_reads_as_maintenance(client, db, env):
    """The new fields must not disturb the ordinary PM/repair row."""
    mt = MaintenanceTypeService().create(code="DSP-MT", name="10,000 KM PMS",
                                         category="PM")
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=env["vehicle"].id, maintenance_type_id=mt.id,
                    scheduled_date=date(2026, 7, 1), user=env["user"],
                    odometer_at_service=10000)
    svc.complete(mo.id, actual_cost=5000, completed_date=date(2026, 7, 5))
    row = next(r for r in _history(client, env) if r["id"] == mo.id)
    assert row["activity_type"] == "PMS"
    assert row["maintenance_type"] == "10,000 KM PMS"
    assert row["reference_number"] is None
    assert row["disposal_value"] is None


# ── the Activity timeline (same page, and the print/report) ─────────

def test_activity_timeline_names_the_order_behind_a_disposal(db, env):
    mo = _disposal(env)
    rows = VehicleActivityHistoryService().get_activity_rows(env["vehicle"])
    row = next(r for r in rows if r["activity_type"] == "Disposal")
    assert row["document_number"] == mo.document_number
    assert row["document_id"] == mo.id
    assert row["reference_number"] == mo.disposal_reference_number
    # What was disposed of, how, and for how much -- on the line itself.
    assert "ADR" in row["description"]
    assert "Manila Scrap Traders" in row["description"]


def test_activity_timeline_names_the_order_behind_a_transfer(db, env):
    mo = _transfer(env)
    rows = VehicleActivityHistoryService().get_activity_rows(env["vehicle"])
    row = next(r for r in rows if r["activity_type"] == "Transfer")
    assert row["document_number"] == mo.document_number
    assert row["reference_number"] == mo.transfer_reference_number
    assert "Cebu Branch" in row["description"]


def test_every_activity_row_can_name_its_source_document(db, env):
    """Acquisition has no document; every other row type must answer
    'which record is this?' without the reader opening a module."""
    _disposal(env)
    rows = VehicleActivityHistoryService().get_activity_rows(env["vehicle"])
    assert all("document_number" in r for r in rows)
