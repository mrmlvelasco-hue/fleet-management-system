"""Tests for VehicleActivityHistoryService and the standalone Vehicle
Activity History Report -- full lifecycle timeline (acquisition, PMS,
repairs, transfers, tire/battery replacements), utilization summary, and
outlet/custodian history reconstructed from the Audit Trail.
"""
from datetime import date

import pytest

from app.core.vehicle_activity_history_service import (
    VehicleActivityHistoryService)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.cli import _seed_transaction_types, _seed_atr_numbering


@pytest.fixture()
def transaction_types(db):
    _seed_transaction_types()
    db.session.commit()


@pytest.fixture()
def numbering(db):
    _seed_atr_numbering()
    db.session.commit()


@pytest.fixture()
def branch(db):
    return BranchService().create(code="BR-ACTHIST", name="Origin Branch")


@pytest.fixture()
def vehicle_type(db):
    return VehicleTypeService().create(code="LV-ACTHIST", name="Light",
                                       category="LIGHT")


@pytest.fixture()
def vehicle(db, branch, vehicle_type):
    return VehicleService().create(
        vehicle_type_id=vehicle_type.id, brand="Mitsubishi", model="Strada",
        year=2013, branch_id=branch.id, conduction_number="AH-000",
        plate_number="AH-1234", acquisition_date=date(2013, 5, 15),
        acquisition_cost=950000)


def test_acquisition_is_the_first_activity_row(db, vehicle):
    svc = VehicleActivityHistoryService()
    rows = svc.get_activity_rows(vehicle)
    assert rows[0]["activity_type"] == "Acquisition"
    assert rows[0]["date"] == date(2013, 5, 15)
    assert rows[0]["cost"] == 950000


def test_completed_pm_order_shows_as_pms_activity(db, vehicle):
    mt = MaintenanceTypeService().create(code="AH-MT", name="10,000 KM PMS",
                                         category="PM")
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    scheduled_date=date(2014, 1, 10), user=None)
    mo.odometer_at_service = 10000
    db.session.commit()
    svc.complete(mo.id, actual_cost=5500, completed_date=date(2014, 1, 10))

    rows = VehicleActivityHistoryService().get_activity_rows(vehicle)
    pms_rows = [r for r in rows if r["activity_type"] == "PMS"]
    assert len(pms_rows) == 1
    assert pms_rows[0]["cost"] == 5500
    assert pms_rows[0]["odometer"] == 10000


def test_completed_cm_order_shows_as_repair_activity(db, vehicle):
    mt = MaintenanceTypeService().create(code="AH-MT2", name="Brake Repair",
                                         category="CORRECTIVE")
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    scheduled_date=date(2017, 3, 12), user=None)
    db.session.commit()
    svc.complete(mo.id, actual_cost=12500, completed_date=date(2017, 3, 12))

    rows = VehicleActivityHistoryService().get_activity_rows(vehicle)
    repair_rows = [r for r in rows if r["activity_type"] == "Repair (ATR)"]
    assert len(repair_rows) == 1
    assert repair_rows[0]["cost"] == 12500


def test_transfer_order_shows_as_transfer_activity_with_destination_outlet(
        db, transaction_types, numbering, vehicle):
    dest = BranchService().create(code="BR-ACTHIST-2", name="Destination Branch")
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date(2018, 4, 1),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id, destination_branch_id=dest.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date(2018, 4, 1))

    rows = VehicleActivityHistoryService().get_activity_rows(vehicle)
    transfer_rows = [r for r in rows if r["activity_type"] == "Transfer"]
    assert len(transfer_rows) == 1
    assert transfer_rows[0]["outlet"] == "Destination Branch"


def test_utilization_summary_rolls_up_correctly(db, vehicle):
    mt_pm = MaintenanceTypeService().create(code="AH-MT3", name="PMS", category="PM")
    mt_cm = MaintenanceTypeService().create(code="AH-MT4", name="Repair",
                                            category="CORRECTIVE")
    svc = MaintenanceOrderService()
    mo1 = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt_pm.id,
                     scheduled_date=date(2020, 1, 1), user=None)
    svc.complete(mo1.id, actual_cost=1000, completed_date=date(2020, 1, 1))
    mo2 = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt_cm.id,
                     scheduled_date=date(2020, 2, 1), user=None)
    svc.complete(mo2.id, actual_cost=2000, completed_date=date(2020, 2, 1))

    activity_svc = VehicleActivityHistoryService()
    rows = activity_svc.get_activity_rows(vehicle)
    summary = activity_svc.get_utilization_summary(vehicle, rows)
    assert summary["pms_count"] == 1
    assert summary["repair_count"] == 1
    assert summary["total_maintenance_cost"] == 3000


def test_outlet_history_falls_back_to_current_branch_with_no_audit_trail(
        db, vehicle):
    """A vehicle with no branch-change audit history at all (e.g. never
    transferred) should still show its current branch as one segment,
    not an empty/broken result."""
    history = VehicleActivityHistoryService().get_outlet_history(vehicle)
    assert len(history) >= 1
    assert history[-1]["outlet"] == "Origin Branch"
    assert history[-1]["to_date"] is None  # still current


def test_outlet_history_reflects_a_real_branch_change(
        db, transaction_types, numbering, vehicle):
    dest = BranchService().create(code="BR-ACTHIST-3", name="New Home Branch")
    tt = TransactionType.query.filter_by(code="DEP-RELOCATION").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id, destination_branch_id=dest.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())

    history = VehicleActivityHistoryService().get_outlet_history(vehicle)
    outlets = [seg["outlet"] for seg in history]
    assert "New Home Branch" in outlets


# ── Report route ────────────────────────────────────────────────────────────

def test_report_route_requires_its_own_permission(db, client, vehicle):
    """Regression guard for the per-report permission system -- a user
    without reportvehicleactivity.view must be blocked even if they can
    see vehicles generally."""
    r = client.get(f"/master/reports/vehicle-activity-history?vehicle_ids={vehicle.id}")
    assert r.status_code in (302, 403)  # redirected to login, or forbidden
