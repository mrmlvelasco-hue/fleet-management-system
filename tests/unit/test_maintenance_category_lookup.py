import pytest
from datetime import date

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, IncompleteChecklistError)
from app.modules.maintenance_config.service import PMScopeTemplateService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-CATCODE", name="Category Code Branch")
    vt = VehicleTypeService().create(code="LV-CATCODE", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="CATCODE-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, vehicle


def test_pm_category_code_still_enforces_checklist_completion(db, env):
    """Confirms the new 'PM' category code (replacing the legacy
    'PREVENTIVE' value) still triggers the checklist-completion rule —
    this is the functional dependency that had to be updated when the
    category codes became a configurable Lookup (PM/CM/INSP/AR/RC/PD)."""
    branch, vt, vehicle = env
    mt = MaintenanceTypeService().create(code="CATCODE-PM", name="PM Category Test",
                                         category="PM")
    scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="PM Category Checklist",
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date.today(), user=None)
    assert order.category == "PM"

    order.status = "IN_PROGRESS"
    from app.extensions import db as _db
    _db.session.commit()

    with pytest.raises(IncompleteChecklistError):
        MaintenanceOrderService().complete(order.id, actual_cost=100,
                                           completed_date=date.today())
