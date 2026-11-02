"""MO create/update propose Vehicle Master Data changes (2026-11-02).

When the order's Transaction Type has an active vehicle-field mapping
(see test_mo_vehicle_mapping_config.py), create()/update() accept an
optional `vehicle_changes` list and record each as a PENDING
MaintenanceOrderVehicleChange row. Vehicle Master Data itself is NEVER
touched at this stage -- only on completion (see
test_mo_vehicle_masterdata_apply.py). old_value is captured from the
vehicle's CURRENT value right now (copy-at-propose-time, never a live
read later).
"""
from datetime import date

import pytest

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, TransactionTypeService,
    InvalidFieldMappingError)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrderVehicleChange)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOVMD", name="MOVMD Branch")
    vt = VehicleTypeService().create(code="LV-MOVMD", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOVMD-000", color="White")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    tt = TransactionTypeService().create(
        code="ADM-CHG-COLOR", name="Change Color",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Vehicle Color",
        input_type="TEXT", is_required=True)
    return branch, vt, vehicle, tt


def test_create_with_a_mapped_type_records_a_pending_change(db, env):
    branch, vt, vehicle, tt = env
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        vehicle_changes=[{"target_field": "color", "new_value": "Red"}])
    rows = MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.target_field == "color"
    assert row.old_value == "White"   # captured from the vehicle NOW
    assert row.new_value == "Red"
    assert row.status == "PENDING"
    assert row.display_label == "New Vehicle Color"


def test_create_never_touches_the_vehicle_itself(db, env):
    branch, vt, vehicle, tt = env
    MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        vehicle_changes=[{"target_field": "color", "new_value": "Red"}])
    db.session.refresh(vehicle)
    assert vehicle.color == "White"  # unchanged until completion


def test_create_without_vehicle_changes_records_nothing(db, env):
    """A Transaction Type with no vehicle_changes supplied behaves
    exactly as before this feature existed."""
    branch, vt, vehicle, tt = env
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id)
    assert MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).count() == 0


def test_create_rejects_a_target_field_outside_the_whitelist(db, env):
    branch, vt, vehicle, tt = env
    with pytest.raises(InvalidFieldMappingError):
        MaintenanceOrderService().create(
            vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2),
            user=None, order_category="OPERATIONAL",
            transaction_type_id=tt.id,
            vehicle_changes=[{"target_field": "plate_number",
                              "new_value": "ABC-123"}])


def test_update_replaces_a_pending_change_with_a_new_value(db, env):
    branch, vt, vehicle, tt = env
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        vehicle_changes=[{"target_field": "color", "new_value": "Red"}])
    MaintenanceOrderService().update(
        order.id, vehicle_changes=[{"target_field": "color",
                                    "new_value": "Blue"}])
    rows = MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).all()
    assert len(rows) == 1
    assert rows[0].new_value == "Blue"
    assert rows[0].status == "PENDING"


def test_unmapped_transaction_type_is_completely_unaffected(db, env):
    """Spec section 11: an order whose Transaction Type has no active
    mapping behaves exactly as it did before this feature existed, even
    if vehicle_changes is (incorrectly) passed."""
    branch, vt, vehicle, tt = env
    tt2 = TransactionTypeService().create(
        code="ADM-CHG-OWNER", name="Change of Ownership",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date(2026, 11, 2), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt2.id)
    assert MaintenanceOrderVehicleChange.query.filter_by(
        order_id=order.id).count() == 0
