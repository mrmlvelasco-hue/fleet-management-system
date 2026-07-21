"""Tests for the Asset Transfer Report (ATR) and Asset Disposal Report
(ADR) -- the Relocation/Transfer and Retirement/Disposal stages of the
Acquisition-to-Retirement vehicle lifecycle, both driven off a completed
Operational Maintenance Order and sharing the exact same Approval Engine
as every other transaction module via BaseTransactionService.
"""
from datetime import date

import pytest

from app.cli import _seed_atr_numbering, _seed_transaction_types
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def numbering(db):
    _seed_atr_numbering()
    db.session.commit()


@pytest.fixture()
def transaction_types(db):
    _seed_transaction_types()
    db.session.commit()


@pytest.fixture()
def origin_branch(db):
    return BranchService().create(code="BR-ORIGIN", name="Sales Department")


@pytest.fixture()
def destination_branch(db):
    return BranchService().create(code="BR-DEST", name="Operations Department")


@pytest.fixture()
def vehicle_type(db):
    return VehicleTypeService().create(code="LV-LIFECYCLE", name="Light",
                                       category="LIGHT")


@pytest.fixture()
def vehicle(db, origin_branch, vehicle_type):
    return VehicleService().create(
        vehicle_type_id=vehicle_type.id, brand="Mitsubishi", model="Strada",
        year=2013, branch_id=origin_branch.id, conduction_number="LC-000")


# ── Asset Transfer Report ────────────────────────────────────────────────────

def test_transfer_moves_vehicle_and_generates_atr_number(
        db, numbering, transaction_types, vehicle, destination_branch):
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id,
                    destination_branch_id=destination_branch.id)

    assert mo.origin_branch_id == vehicle.branch_id  # snapshotted at creation

    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(vehicle)
    db.session.refresh(mo)

    assert vehicle.branch_id == destination_branch.id
    assert mo.transfer_reference_number is not None
    assert mo.transfer_reference_number.startswith("ATR-")


def test_atr_reference_number_not_regenerated_on_second_complete_call(
        db, numbering, transaction_types, vehicle, destination_branch):
    """complete() being called again (e.g. a re-save) must not mint a
    second ATR number for the same order."""
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id,
                    destination_branch_id=destination_branch.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(mo)
    first_number = mo.transfer_reference_number

    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(mo)
    assert mo.transfer_reference_number == first_number


def test_order_without_destination_branch_does_not_transfer(
        db, numbering, transaction_types, vehicle):
    """An Operational order with no destination branch set (e.g. a plain
    Administrative order) must never touch branch_id or generate a
    transfer reference number."""
    original_branch_id = vehicle.branch_id
    tt = TransactionType.query.filter_by(code="ADM-MIGRATE-EXPENSE").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=100, completed_date=date.today())
    db.session.refresh(vehicle)
    assert vehicle.branch_id == original_branch_id
    assert mo.transfer_reference_number is None


# ── Asset Disposal Report ────────────────────────────────────────────────────

@pytest.mark.parametrize("code", [
    "DIS-SCRAPPAGE", "DIS-CARNAPPED", "DIS-TOTAL-LOSS",
    "DIS-UNECONOMICAL", "DIS-SOLD", "DIS-DONATED",
])
def test_every_disposal_method_retires_the_vehicle(
        db, numbering, transaction_types, vehicle, code):
    tt = TransactionType.query.filter_by(code=code).first()
    assert tt is not None, f"{code} was not seeded by _seed_transaction_types"
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(vehicle)
    db.session.refresh(mo)

    assert vehicle.status == "DISPOSED"
    assert mo.disposal_reference_number is not None
    assert mo.disposal_reference_number.startswith("ADR-")


def test_disposal_value_and_recipient_are_optional_and_preserved(
        db, numbering, transaction_types, vehicle):
    tt = TransactionType.query.filter_by(code="DIS-SOLD").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id,
                    disposal_value=150000, disposal_recipient="ABC Motors")
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(mo)
    assert mo.disposal_value == 150000
    assert mo.disposal_recipient == "ABC Motors"


def test_disposal_without_value_or_recipient_still_retires_vehicle(
        db, numbering, transaction_types, vehicle):
    """Per spec, disposal_value/recipient are optional -- completing a
    disposal order with neither set must still succeed."""
    tt = TransactionType.query.filter_by(code="DIS-CARNAPPED").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())
    db.session.refresh(vehicle)
    assert vehicle.status == "DISPOSED"


def test_non_disposal_order_does_not_retire_vehicle(
        db, numbering, transaction_types, vehicle):
    tt = TransactionType.query.filter_by(code="ADM-MIGRATE-EXPENSE").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=100, completed_date=date.today())
    db.session.refresh(vehicle)
    assert vehicle.status != "DISPOSED"
    assert mo.disposal_reference_number is None


# ── Active-list visibility (hide from active lists, keep in reports) ────────

def test_disposed_vehicle_excluded_from_default_active_list(
        db, numbering, transaction_types, vehicle):
    tt = TransactionType.query.filter_by(code="DIS-SCRAPPAGE").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())

    active_ids = [v.id for v in VehicleService().list(include_inactive=True)]
    assert vehicle.id not in active_ids


def test_disposed_vehicle_visible_with_explicit_opt_in(
        db, numbering, transaction_types, vehicle):
    tt = TransactionType.query.filter_by(code="DIS-SCRAPPAGE").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())

    all_ids = [v.id for v in VehicleService().list(
        include_inactive=True, include_disposed=True)]
    assert vehicle.id in all_ids


def test_disposed_vehicle_still_appears_in_register_report(
        db, numbering, transaction_types, vehicle):
    from app.modules.master_data.vehicle.report_service import (
        VehicleRegisterReportService)
    tt = TransactionType.query.filter_by(code="DIS-SCRAPPAGE").first()
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())

    rows = VehicleRegisterReportService().get_rows()
    plates = [r["plate_number"] for r in rows]
    assert vehicle.conduction_number in plates
