"""Tests for the Maintenance Order lifecycle guards:

  DRAFT -> SUBMITTED -> FOR APPROVAL -> (RETURNED -> RESUBMITTED ->
  FOR APPROVAL) -> APPROVED -> Start Work -> COMPLETED

Specifically the three gaps reported from real use:
  1. A RETURNED order offered only Cancel -- no way to resubmit.
  2. Work could start straight from DRAFT, skipping approval entirely.
  3. A vehicle could be queued for a second PM order while one was
     already open.
"""
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, InvalidOrderStateError)


@pytest.fixture()
def env(db):
    _seed_transaction_types()
    db.session.commit()
    vt = VehicleTypeService().create(code="LV-LC", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-LC", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-LC", name="LC Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="LC-000")
    return vt, mt, branch, vehicle


def test_work_cannot_start_before_submission(db, env):
    """A brand-new DRAFT order was never submitted, so there is nothing
    approved -- starting work must be refused server-side, not merely
    hidden in the UI."""
    vt, mt, branch, vehicle = env
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    assert mo.status == "DRAFT"
    with pytest.raises(InvalidOrderStateError) as excinfo:
        MaintenanceOrderService().start_work(mo.id)
    assert "not been submitted" in str(excinfo.value)
    assert mo.status == "DRAFT"  # unchanged


def test_work_cannot_start_while_approval_is_pending(db, env):
    """Submitted but not yet approved -- work must still be refused."""
    vt, mt, branch, vehicle = env
    from app.core.approval.models import ApprovalInstance
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    instance = ApprovalInstance(
        document_type_id=1, reference_table="maintenance_orders",
        reference_id=mo.id, status="PENDING")
    db.session.add(instance)
    db.session.flush()
    mo.approval_instance_id = instance.id
    db.session.commit()

    with pytest.raises(InvalidOrderStateError) as excinfo:
        MaintenanceOrderService().start_work(mo.id)
    assert "not approved" in str(excinfo.value).lower()


def test_work_starts_once_approved(db, env):
    """The happy path must remain unblocked: an APPROVED order starts."""
    vt, mt, branch, vehicle = env
    from app.core.approval.models import ApprovalInstance
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    instance = ApprovalInstance(
        document_type_id=1, reference_table="maintenance_orders",
        reference_id=mo.id, status="APPROVED")
    db.session.add(instance)
    db.session.flush()
    mo.approval_instance_id = instance.id
    db.session.commit()

    MaintenanceOrderService().start_work(mo.id)
    assert mo.status == "IN_PROGRESS"


def test_second_open_pm_order_for_same_vehicle_is_blocked(db, env):
    """A vehicle already queued for a PM must not be queued again for
    the same maintenance type while that order is still open."""
    vt, mt, branch, vehicle = env
    MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    with pytest.raises(InvalidOrderStateError) as excinfo:
        MaintenanceOrderService().create(
            vehicle_id=vehicle.id, maintenance_type_id=mt.id,
            scheduled_date=date.today(), user=None)
    assert "already has an open" in str(excinfo.value)


def test_new_pm_order_allowed_once_previous_is_completed(db, env):
    """The guard must only block OPEN orders -- once the previous one is
    completed, the next PM can be raised normally."""
    vt, mt, branch, vehicle = env
    svc = MaintenanceOrderService()
    first = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                       scheduled_date=date.today(), user=None)
    first.status = "COMPLETED"
    db.session.commit()

    second = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                        scheduled_date=date.today(), user=None)
    assert second.id != first.id


def test_cancelled_order_does_not_block_a_new_one(db, env):
    vt, mt, branch, vehicle = env
    svc = MaintenanceOrderService()
    first = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                       scheduled_date=date.today(), user=None)
    first.status = "CANCELLED"
    db.session.commit()

    second = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                        scheduled_date=date.today(), user=None)
    assert second.id != first.id


def test_operational_orders_are_not_subject_to_the_pm_duplicate_guard(
        db, env):
    """Operational orders (deployment, admin, disposal) aren't PM work --
    a vehicle can legitimately have several open at once."""
    vt, mt, branch, vehicle = env
    from app.modules.transactions.maintenance_order.models import (
        TransactionType)
    tt = TransactionType.query.filter_by(code="DEP-ASSIGNMENT").first()
    svc = MaintenanceOrderService()
    a = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                   user=None, order_category="OPERATIONAL",
                   transaction_type_id=tt.id)
    b = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                   user=None, order_category="OPERATIONAL",
                   transaction_type_id=tt.id)
    assert a.id != b.id
