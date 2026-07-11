from datetime import date, datetime, timezone

import pytest

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, IncompleteChecklistError)
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-MO", name="MO Branch")
    vt = VehicleTypeService().create(code="LV-MO", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(
        code="PMS-5K-MO", name="5,000 KM PMS", category="PREVENTIVE")
    mt_corrective = MaintenanceTypeService().create(
        code="CM-MO", name="Corrective Repair", category="CORRECTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MO-000",
        current_odometer=4800)
    user = User(username="mo_requester", email="mo@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="5K Scope", items=[
            {"activity_code": "OIL", "activity_description": "Change Oil",
             "sort_order": 1},
            {"activity_code": "FILTER", "activity_description": "Replace Filter",
             "sort_order": 2},
        ])
    return vehicle, mt, mt_corrective, scope, user


def test_create_preventive_order_generates_checklist_from_scope(db, env):
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date(2026, 7, 20),
        odometer_at_service=5000, user=user)
    assert order.document_number.startswith("MO-")
    assert order.category == "PREVENTIVE"
    assert len(order.checklist_items) == 2
    assert order.checklist_items[0].activity_code == "OIL"


def test_create_corrective_order_has_no_checklist_requirement(db, env):
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt_corrective.id,
        scheduled_date=date(2026, 7, 20), user=user)
    assert order.category == "CORRECTIVE"
    assert len(order.checklist_items) == 0


def test_complete_blocked_when_checklist_incomplete(db, env):
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date(2026, 7, 20),
        odometer_at_service=5000, user=user)
    svc.submit(order.id, user=user)
    svc.start_work(order.id)
    with pytest.raises(IncompleteChecklistError):
        svc.complete(order.id, actual_cost=3500, completed_date=date(2026, 7, 21))


def test_complete_succeeds_after_checklist_done(db, env):
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date(2026, 7, 20),
        odometer_at_service=5000, user=user)
    svc.submit(order.id, user=user)
    svc.start_work(order.id)
    for item in order.checklist_items:
        svc.toggle_checklist_item(item.id, done=True, user=user)
    svc.complete(order.id, actual_cost=3500, completed_date=date(2026, 7, 21))
    completed = db.session.get(MaintenanceOrder, order.id)
    assert completed.status == "COMPLETED"
    assert completed.actual_cost == 3500
    # Vehicle odometer should bump to the higher service reading
    assert db.session.get(type(vehicle), vehicle.id).current_odometer == 5000


def test_corrective_complete_without_checklist_ok(db, env):
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt_corrective.id,
        scheduled_date=date(2026, 7, 20), user=user)
    svc.submit(order.id, user=user)
    svc.start_work(order.id)
    svc.complete(order.id, actual_cost=1200, completed_date=date(2026, 7, 21))
    assert db.session.get(MaintenanceOrder, order.id).status == "COMPLETED"


def test_toggle_checklist_item_only_while_in_progress(db, env):
    from app.modules.transactions.maintenance_order.service import (
        InvalidOrderStateError)
    vehicle, mt, mt_corrective, scope, user = env
    svc = MaintenanceOrderService()
    order = svc.create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date(2026, 7, 20),
        odometer_at_service=5000, user=user)
    item = order.checklist_items[0]
    with pytest.raises(InvalidOrderStateError):
        svc.toggle_checklist_item(item.id, done=True, user=user)
