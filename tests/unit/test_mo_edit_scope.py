"""Tests for editing a Maintenance Order after it has been raised.

The initiator asked to be able to correct or add remarks to an order.
The subtlety is that "DRAFT" is not one state in this system -- an order
stays DRAFT while its approval is PENDING -- so the edit rules key off
the approval state as well, not just order.status.
"""
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.approval.models import ApprovalInstance
from app.core.security.registry import sync_permissions
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, InvalidOrderStateError)


@pytest.fixture()
def order(db):
    sync_permissions()
    _seed_transaction_types()
    db.session.commit()
    vt = VehicleTypeService().create(code="LV-ED", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-ED", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-ED", name="Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="ED-0")
    return MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)


def _attach_approval(db, order, status):
    instance = ApprovalInstance(
        document_type_id=1, reference_table="maintenance_orders",
        reference_id=order.id, status=status)
    db.session.add(instance)
    db.session.flush()
    order.approval_instance_id = instance.id
    db.session.commit()
    db.session.expire_all()
    return db.session.get(MaintenanceOrder, order.id)


def test_unsubmitted_draft_is_fully_editable(db, order):
    assert MaintenanceOrderService().editable_scope(order) == "FULL"


def test_description_can_be_updated_on_a_draft(db, order):
    MaintenanceOrderService().update(
        order.id, description="corrected remarks")
    assert order.description == "corrected remarks"


def test_order_awaiting_approval_cannot_be_edited(db, order):
    """An approver is reviewing it -- changing the particulars underneath
    them would mean they approve something they never saw."""
    fresh = _attach_approval(db, order, "PENDING")
    svc = MaintenanceOrderService()
    assert svc.editable_scope(fresh) == "NONE"
    with pytest.raises(InvalidOrderStateError) as exc:
        svc.update(fresh.id, description="sneaky change")
    assert "awaiting approval" in str(exc.value)


def test_returned_order_is_editable_again(db, order):
    """Being able to correct it is the entire point of a return."""
    fresh = _attach_approval(db, order, "RETURNED")
    svc = MaintenanceOrderService()
    assert svc.editable_scope(fresh) == "FULL"
    svc.update(fresh.id, description="addressed the feedback")
    assert fresh.description == "addressed the feedback"


def test_in_progress_allows_remarks_only(db, order):
    fresh = _attach_approval(db, order, "APPROVED")
    fresh.status = "IN_PROGRESS"
    db.session.commit()

    svc = MaintenanceOrderService()
    assert svc.editable_scope(fresh) == "REMARKS_ONLY"
    svc.update(fresh.id, description="found a seized caliper")
    assert fresh.description == "found a seized caliper"


def test_in_progress_rejects_changes_to_approved_particulars(db, order):
    """Work is underway against what was approved; cost/type/scope must
    not move underneath it."""
    fresh = _attach_approval(db, order, "APPROVED")
    fresh.status = "IN_PROGRESS"
    db.session.commit()

    with pytest.raises(InvalidOrderStateError) as exc:
        MaintenanceOrderService().update(fresh.id, estimated_cost=99999)
    assert "only the description" in str(exc.value).lower()


def test_completed_order_cannot_be_edited(db, order):
    order.status = "COMPLETED"
    db.session.commit()
    svc = MaintenanceOrderService()
    assert svc.editable_scope(order) == "NONE"
    with pytest.raises(InvalidOrderStateError):
        svc.update(order.id, description="too late")


def test_cancelled_order_cannot_be_edited(db, order):
    order.status = "CANCELLED"
    db.session.commit()
    assert MaintenanceOrderService().editable_scope(order) == "NONE"


def test_edits_are_captured_in_the_audit_trail(db, order):
    """No extra bookkeeping needed -- the existing audit listeners record
    who changed what."""
    from app.core.models.audit_log import AuditLog
    before = AuditLog.query.filter_by(
        table_name="maintenance_orders", record_id=order.id).count()
    MaintenanceOrderService().update(order.id, description="audited change")
    after = AuditLog.query.filter_by(
        table_name="maintenance_orders", record_id=order.id).count()
    assert after > before
