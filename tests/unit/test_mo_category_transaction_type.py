from datetime import date

import pytest

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, TransactionTypeService, InvalidOrderCategoryError)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MOCAT", name="MO Category Branch")
    vt = VehicleTypeService().create(code="LV-MOCAT", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOCAT-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, vehicle


def test_transaction_type_belongs_to_exactly_one_category(db):
    tt = TransactionTypeService().create(
        code="DEP-ASSIGN", name="Assignment", order_category="OPERATIONAL",
        group="DEPLOYMENT")
    assert tt.order_category == "OPERATIONAL"
    assert tt.group == "DEPLOYMENT"


def test_existing_maintenance_order_defaults_to_maintenance_category(db, env):
    """Backward compatibility: every existing/new PM/CM order, created
    the same way as before, must default to Category=MAINTENANCE without
    any change to the caller."""
    branch, vt, vehicle = env
    mt = MaintenanceTypeService().create(code="MOCAT-MT", name="MO Category Test",
                                         category="PM")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    assert order.order_category == "MAINTENANCE"


def test_operational_order_does_not_require_maintenance_type(db, env):
    """The exact rule requested: Operational category orders are normal
    work requests and must NOT need a Maintenance Type or PM Scope
    Template at all."""
    branch, vt, vehicle = env
    tt = TransactionTypeService().create(
        code="DEP-ASSIGN2", name="Assignment", order_category="OPERATIONAL",
        group="DEPLOYMENT")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        transaction_type_id=tt.id, scheduled_date=date.today(),
        description="Assign vehicle to new branch", user=None)
    assert order.maintenance_type_id is None
    assert order.scope_template_id is None
    assert order.transaction_type_id == tt.id


def test_maintenance_category_order_still_requires_maintenance_type(db, env):
    """Confirms the existing rule is preserved for Category=MAINTENANCE
    (or when order_category isn't specified at all -- the default)."""
    branch, vt, vehicle = env
    with pytest.raises(InvalidOrderCategoryError):
        MaintenanceOrderService().create(
            vehicle_id=vehicle.id, order_category="MAINTENANCE",
            scheduled_date=date.today(), user=None)


def test_operational_order_rejects_mismatched_transaction_type_category(db, env):
    """A Transaction Type belonging to a DIFFERENT category than the
    order's own order_category is a data-integrity error, not silently
    accepted."""
    branch, vt, vehicle = env
    maintenance_tt = TransactionTypeService().create(
        code="MAINT-SERVICING", name="Servicing", order_category="MAINTENANCE",
        group="MAINTENANCE")
    with pytest.raises(InvalidOrderCategoryError):
        MaintenanceOrderService().create(
            vehicle_id=vehicle.id, order_category="OPERATIONAL",
            transaction_type_id=maintenance_tt.id,
            scheduled_date=date.today(), user=None)


def test_list_transaction_types_by_category(db):
    TransactionTypeService().create(code="DEP-REASSIGN", name="Reassignment",
                                    order_category="OPERATIONAL", group="DEPLOYMENT")
    TransactionTypeService().create(code="MAINT-REPAIR", name="Repair",
                                    order_category="MAINTENANCE", group="MAINTENANCE")
    operational_types = TransactionTypeService().list(order_category="OPERATIONAL")
    codes = [t.code for t in operational_types]
    assert "DEP-REASSIGN" in codes
    assert "MAINT-REPAIR" not in codes
