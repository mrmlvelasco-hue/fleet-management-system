"""MO complete() applies PENDING Vehicle Master Data changes atomically
(2026-11-02).

Unlike the existing driver/branch-transfer/disposal completion hooks
(each its own commit AFTER the main completion commit -- documented
pre-existing KI-07 issue), this apply step runs BEFORE that commit so a
stale-value conflict blocks completion outright rather than completing
the order and silently skipping the vehicle write.
"""
from datetime import date

import pytest

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, TransactionTypeService,
    VehicleMasterDataConflictError)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrderVehicleChange)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOVMDA", name="MOVMDA Branch")
    vt = VehicleTypeService().create(code="LV-MOVMDA", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOVMDA-000", color="White")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    tt = TransactionTypeService().create(
        code="ADM-CHG-COLOR-A", name="Change Color",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Vehicle Color",
        input_type="TEXT", is_required=True)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        vehicle_changes=[{"target_field": "color", "new_value": "Red"}])
    return branch, vt, vehicle, tt, order


def test_complete_applies_the_pending_change_to_the_vehicle(db, env):
    branch, vt, vehicle, tt, order = env
    MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2))
    db.session.refresh(vehicle)
    assert vehicle.color == "Red"


def test_complete_marks_the_change_row_applied_with_who_and_when(db, env):
    branch, vt, vehicle, tt, order = env
    from app.modules.user_management.models import User
    user = User.query.first()
    MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2),
                                       user=user)
    row = MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).first()
    assert row.status == "APPLIED"
    assert row.applied_at is not None
    if user is not None:
        assert row.applied_by == user.id


def test_complete_blocks_when_the_vehicle_value_changed_since_proposal(db, env):
    """old_value was captured as 'White' at propose time. If the
    vehicle's color is no longer 'White' when the order is completed,
    applying 'Red' blindly would silently overwrite a value the
    approver never actually saw -- so completion is blocked instead."""
    branch, vt, vehicle, tt, order = env
    vehicle.color = "Green"  # changed by something else in between
    db.session.commit()
    with pytest.raises(VehicleMasterDataConflictError):
        MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2))
    db.session.refresh(vehicle)
    assert vehicle.color == "Green"  # never overwritten


def test_a_conflict_does_not_complete_the_order_either(db, env):
    """The block must be atomic with order completion, not just the
    vehicle write: if one fails, neither happens."""
    branch, vt, vehicle, tt, order = env
    vehicle.color = "Green"
    db.session.commit()
    with pytest.raises(VehicleMasterDataConflictError):
        MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2))
    db.session.refresh(order)
    assert order.status != "COMPLETED"


def test_completing_again_does_not_reapply_an_already_applied_row(db, env):
    """No double-completion guard exists on complete() itself (a
    pre-existing gap, same root cause as KI-07) -- this logic must be
    self-idempotent: only PENDING rows are ever touched."""
    branch, vt, vehicle, tt, order = env
    MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2))
    vehicle.color = "SomeoneElseChangedIt"
    db.session.commit()
    # Calling complete() again must NOT raise a conflict and must NOT
    # touch the vehicle again -- the row is already APPLIED, not PENDING.
    MaintenanceOrderService().complete(order.id, None, date(2026, 11, 2))
    db.session.refresh(vehicle)
    assert vehicle.color == "SomeoneElseChangedIt"


def test_an_order_with_no_pending_changes_completes_exactly_as_before(db):
    """An order whose Transaction Type has no mapping (the overwhelming
    majority, today) must complete exactly as it always has."""
    branch = BranchService().create(code="BR-MOVMDA2", name="MOVMDA Branch 2")
    vt = VehicleTypeService().create(code="LV-MOVMDA2", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOVMDA2-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL")
    completed = MaintenanceOrderService().complete(order.id, None,
                                                    date(2026, 11, 2))
    assert completed.status == "COMPLETED"


def test_cancel_marks_pending_changes_cancelled_without_touching_vehicle(db, env):
    branch, vt, vehicle, tt, order = env
    MaintenanceOrderService().cancel(order.id, user=None)
    db.session.refresh(vehicle)
    assert vehicle.color == "White"  # unchanged
    row = MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).first()
    assert row.status == "CANCELLED"
