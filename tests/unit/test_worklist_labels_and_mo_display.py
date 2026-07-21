"""Tests for:
1. get_worklist_labels() -- plate number + type label resolution for the
   "For My Action" dashboard worklist, so an approver can identify which
   vehicle/request each pending item is without opening it first.
2. The underlying data gap it surfaces: an Operational Maintenance Order
   (Assignment/Transfer/Disposal etc.) has no maintenance_type at all, so
   `mo.maintenance_type.name` rendered blank and `mo.category` rendered
   literally "None" in the MO list, Vehicle Detail history, and Vehicle
   Print history tables -- reported as "we can't find the transfer
   activity in the vehicle report" when the activity was actually there,
   just displayed unreadably.
"""
from datetime import date

import pytest

from app.core.reference_resolver import get_worklist_labels
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
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
def vehicle(db):
    branch = BranchService().create(code="BR-WORKLIST", name="Worklist Branch")
    vt = VehicleTypeService().create(code="LV-WORKLIST", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Mitsubishi", model="Strada", year=2013,
        branch_id=branch.id, conduction_number="WL-000", plate_number="WL-1234")
    return v


def test_worklist_labels_resolve_plate_and_transaction_type(
        db, transaction_types, vehicle):
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date.today(), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id)

    labels = get_worklist_labels("maintenance_orders", mo.id)
    assert labels["plate_number"] == "WL-1234"
    assert labels["type_label"] == "Transfer / Relocation"


def test_worklist_labels_use_maintenance_type_for_maintenance_orders(db, vehicle):
    from app.modules.master_data.reference.service import MaintenanceTypeService
    mt = MaintenanceTypeService().create(code="WL-MT", name="Oil Change",
                                         category="PM")
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)

    labels = get_worklist_labels("maintenance_orders", mo.id)
    assert labels["plate_number"] == "WL-1234"
    assert labels["type_label"] == "Oil Change"


def test_worklist_labels_return_none_gracefully_for_unregistered_table(db):
    labels = get_worklist_labels("not_a_real_table", 999)
    assert labels == {"plate_number": None, "type_label": None}


def test_worklist_labels_return_none_gracefully_for_missing_record(db):
    labels = get_worklist_labels("maintenance_orders", 999999)
    assert labels == {"plate_number": None, "type_label": None}


# ── MO list / Vehicle Detail / Vehicle Print Type+Category display ─────────

def test_operational_order_appears_correctly_in_vehicle_detail_history(
        db, transaction_types, numbering, vehicle):
    """The actual reported symptom: a completed Operational (Transfer)
    order must show up in the vehicle's own Maintenance History with a
    readable type -- not silently missing, and not showing a bare
    'None'."""
    tt = TransactionType.query.filter_by(code="DEP-TRANSFER").first()
    other_branch = BranchService().create(code="BR-WORKLIST-2", name="Other Branch")
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, scheduled_date=date.today(),
                    user=None, order_category="OPERATIONAL",
                    transaction_type_id=tt.id,
                    destination_branch_id=other_branch.id)
    svc.complete(mo.id, actual_cost=0, completed_date=date.today())

    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    history = (MaintenanceOrder.query
              .filter_by(vehicle_id=vehicle.id, status="COMPLETED").all())
    assert mo.id in [h.id for h in history]
    # This is the actual display data the templates now use as a fallback:
    assert history[0].maintenance_type is None  # confirms why it was blank
    assert history[0].transaction_type.name == "Transfer / Relocation"
    assert history[0].category is None  # confirms why it showed literal "None"
    assert history[0].order_category == "OPERATIONAL"  # the fallback value
