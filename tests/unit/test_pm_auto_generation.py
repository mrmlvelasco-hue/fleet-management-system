from datetime import date

import pytest

from app.modules.transactions.maintenance_order.tasks import (
    auto_generate_due_maintenance_orders)
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.system_admin.models import NotificationRule, InAppNotification
from app.modules.user_management.models import User, Role


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-AUTO", name="Auto Branch")
    vt = VehicleTypeService().create(code="LV-AUTO", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(
        code="PMS-5K-AUTO", name="5,000 KM PMS", category="PREVENTIVE")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                              trigger_mode="KM", interval_km=5000)

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    fleet_admin_role = Role(name="Fleet Admin AUTO")
    admin_user = User(username="fleet_admin_auto", email="fa@x.com",
                      password_hash="x")
    admin_user.roles.append(fleet_admin_role)
    import app.core.approval.engine as _engine
    _engine._subscribers.clear()
    from app.modules.system_admin.services.notification_engine import (
        register_notification_hooks)
    register_notification_hooks()

    from app.extensions import db as _db
    _db.session.add_all([fleet_admin_role, admin_user])
    _db.session.flush()  # assign fleet_admin_role.id before referencing it below
    _db.session.add(NotificationRule(event_code="pm_overdue", channel="IN_APP",
                                     recipient_type="ROLE",
                                     role_id=fleet_admin_role.id))
    _db.session.add(NotificationRule(event_code="pm_due_soon", channel="IN_APP",
                                     recipient_type="ROLE",
                                     role_id=fleet_admin_role.id))
    _db.session.commit()
    return branch, vt, mt, admin_user


def _make_vehicle(branch, vt, odometer, tag):
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number=f"AUTO-{tag}",
        current_odometer=odometer)


def test_default_policy_does_not_auto_create_maintenance_order(db, env):
    """PMS-3 behavior change: AUTO_SCHEDULE is now the default (Option B,
    recommended) — the system notifies but does NOT auto-create a
    Maintenance Order. This replaces the old default, which always
    auto-created one directly (Option C-like, not recommended)."""
    branch, vt, mt, admin_user = env
    vehicle = _make_vehicle(branch, vt, 5200, "1")
    created = auto_generate_due_maintenance_orders()
    assert created == 0
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert order is None


def test_auto_mo_policy_still_creates_draft_order(db, env):
    """Explicitly opting into AUTO_MO preserves the old literal behavior
    for anyone who wants it back."""
    branch, vt, mt, admin_user = env
    schedule = PMScheduleService().list()[0]
    schedule.next_pms_generation = "AUTO_MO"
    from app.extensions import db as _db
    _db.session.commit()

    vehicle = _make_vehicle(branch, vt, 5200, "1b")
    created = auto_generate_due_maintenance_orders()
    assert created == 1
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert order is not None
    assert order.status == "DRAFT"


def test_manual_policy_creates_no_order(db, env):
    branch, vt, mt, admin_user = env
    schedule = PMScheduleService().list()[0]
    schedule.next_pms_generation = "MANUAL"
    from app.extensions import db as _db
    _db.session.commit()

    vehicle = _make_vehicle(branch, vt, 5200, "1c")
    created = auto_generate_due_maintenance_orders()
    assert created == 0
    assert MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).count() == 0


def test_notifications_fire_regardless_of_generation_policy(db, env):
    """Visibility (dashboard + notifications) is independent of whether a
    document gets auto-created — even MANUAL/AUTO_SCHEDULE vehicles should
    still notify so fleet staff know action is needed."""
    branch, vt, mt, admin_user = env
    _make_vehicle(branch, vt, 5200, "1d")
    auto_generate_due_maintenance_orders()
    notifs = InAppNotification.query.filter_by(
        user_id=admin_user.id, event_code="pm_overdue").all()
    assert len(notifs) == 1


def test_auto_generation_is_idempotent(db, env):
    branch, vt, mt, admin_user = env
    schedule = PMScheduleService().list()[0]
    schedule.next_pms_generation = "AUTO_MO"
    from app.extensions import db as _db
    _db.session.commit()
    vehicle = _make_vehicle(branch, vt, 5200, "2")
    auto_generate_due_maintenance_orders()
    created_again = auto_generate_due_maintenance_orders()
    assert created_again == 0
    assert MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).count() == 1


def test_auto_generation_fires_notification(db, env):
    branch, vt, mt, admin_user = env
    vehicle = _make_vehicle(branch, vt, 5200, "3")
    auto_generate_due_maintenance_orders()
    notifs = InAppNotification.query.filter_by(
        user_id=admin_user.id, event_code="pm_overdue").all()
    assert len(notifs) == 1


def test_good_vehicles_produce_no_orders(db, env):
    branch, vt, mt, admin_user = env
    _make_vehicle(branch, vt, 100, "4")
    created = auto_generate_due_maintenance_orders()
    assert created == 0


def test_auto_generation_uses_schedule_linked_scope_template(db, env):
    from app.modules.maintenance_config.service import PMScopeTemplateService
    branch, vt, mt, admin_user = env
    schedule = PMScheduleService().list()[0]
    schedule.next_pms_generation = "AUTO_MO"
    from app.extensions import db as _db
    _db.session.commit()
    PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Linked Scope",
        pm_schedule_id=schedule.id,
        items=[{"activity_code": "OIL", "activity_description": "Change Oil",
               "sort_order": 1}])
    vehicle = _make_vehicle(branch, vt, 5200, "5")
    auto_generate_due_maintenance_orders()
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert len(order.checklist_items) == 1
    assert order.checklist_items[0].activity_code == "OIL"
