from datetime import date, timedelta

import pytest

from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.transactions.vehicle_registration.tasks import (
    auto_generate_due_registrations)
from app.modules.system_admin.models import InAppNotification
from app.modules.user_management.models import User, Role, Permission


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-REGTASK", name="Reg Task Branch")
    vt = VehicleTypeService().create(code="LV-REGTASK", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="REGTASK-000")
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 + 5), user=None)
    reg.status = "COMPLETED"
    from app.extensions import db as _db
    _db.session.commit()

    role = Role(name="RegTaskAdminRole")
    p = Permission(code="vehicleregistration.view", module="vehicleregistration",
                   action="view")
    _db.session.add(p)
    role.permissions.append(p)
    admin_user = User(username="regtask_admin", email="regtask_admin@x.com",
                      password_hash="x")
    admin_user.roles.append(role)
    _db.session.add_all([role, admin_user])
    _db.session.commit()

    return branch, vt, vehicle, admin_user


def test_default_policy_does_not_auto_create_registration(db, env):
    branch, vt, vehicle, admin_user = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    created = auto_generate_due_registrations()
    assert created == 0
    open_regs = VehicleRegistration.query.filter_by(
        vehicle_id=vehicle.id, registration_type="RENEWAL").count()
    assert open_regs == 0


def test_auto_registration_policy_creates_draft_renewal(db, env):
    branch, vt, vehicle, admin_user = env
    RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3, notify_before_days=30,
        next_generation_policy="AUTO_REGISTRATION")
    created = auto_generate_due_registrations()
    assert created == 1
    renewal = VehicleRegistration.query.filter_by(
        vehicle_id=vehicle.id, registration_type="RENEWAL").first()
    assert renewal is not None
    assert renewal.status == "DRAFT"


def test_manual_policy_creates_no_registration(db, env):
    branch, vt, vehicle, admin_user = env
    RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3, notify_before_days=30,
        next_generation_policy="MANUAL")
    created = auto_generate_due_registrations()
    assert created == 0


def test_idempotent_no_duplicate_renewal(db, env):
    branch, vt, vehicle, admin_user = env
    RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3, notify_before_days=30,
        next_generation_policy="AUTO_REGISTRATION")
    auto_generate_due_registrations()
    created_again = auto_generate_due_registrations()
    assert created_again == 0
    assert VehicleRegistration.query.filter_by(
        vehicle_id=vehicle.id, registration_type="RENEWAL").count() == 1


def test_notifications_fire_regardless_of_policy(db, env):
    branch, vt, vehicle, admin_user = env
    from app.modules.system_admin.models import NotificationRule
    rule = NotificationRule(event_code="registration_overdue", channel="IN_APP",
                            recipient_type="SPECIFIC_USER", user_id=admin_user.id,
                            is_active=True)
    from app.extensions import db as _db
    _db.session.add(rule)
    _db.session.commit()

    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    auto_generate_due_registrations()
    notifs = InAppNotification.query.filter_by(
        user_id=admin_user.id, event_code="registration_overdue").all()
    assert len(notifs) == 1


def test_good_vehicles_produce_no_registrations(db, env):
    branch, vt, vehicle, admin_user = env
    vehicle2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="REGTASK-001")
    reg2 = VehicleRegistrationService().create(
        vehicle_id=vehicle2.id, registration_type="NEW",
        registration_date=date.today(), user=None)
    reg2.status = "COMPLETED"
    db.session.commit()
    RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3, notify_before_days=30,
        next_generation_policy="AUTO_REGISTRATION")
    created = auto_generate_due_registrations()
    # Only the OVERDUE vehicle from the fixture, not the freshly-registered one
    assert created == 1
