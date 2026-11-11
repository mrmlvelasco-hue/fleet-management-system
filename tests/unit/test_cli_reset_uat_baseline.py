"""Tests for `flask reset uat-baseline` -- the more destructive sibling
of `flask reset transactions`, meant for seeding a UAT/production
cutover from a copy of the dev database: it ALSO wipes vehicles,
vendors, drivers/assignees and their linked login accounts, leaving a
clean, fully-configured system with zero dummy master data.

`flask reset transactions` itself (app/cli.py `_reset_transactions` /
`reset_transactions`) must not be touched by any of this -- its own
test (test_vehicle_import_and_reset.py) is left unmodified and must
keep passing.
"""
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.cli import _reset_transactions, _reset_uat_baseline
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment
from app.modules.master_data.vehicle.odometer_models import VehicleOdometerLog
from app.modules.master_data.vendor.models import (
    Vendor, VendorContact, vendor_branches)
from app.modules.master_data.driver.models import Driver, EmergencyContact
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.battery.models import Battery
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.vehicle_handover.models import (
    VehicleHandoverChecklist, HandoverItem)
from app.modules.transactions.vehicle_checklist.models import (
    ChecklistTemplate, VehicleChecklist)
from app.modules.transactions.fuel.models import FuelCard, FuelTransaction
from app.modules.user_management.models import User, Role, Permission
from app.modules.approval_config.models import ApprovalPath, ApprovalLevel
from app.modules.system_admin.models import NotificationRule


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="TRK", name="Truck",
                                     category="HEAVY")
    branch = BranchService().create(code="MNL", name="Manila")
    return vt, branch


def _vehicle(vt, branch, conduction_number="UAT-1"):
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number=conduction_number)


def _vendor(code="VEN-1"):
    v = Vendor(code=code, name="Acme Repairs", vendor_type="GOODS")
    db.session.add(v)
    db.session.commit()
    return v


def _driver(branch, employee_number="EMP-001", user=None):
    d = Driver(employee_number=employee_number, first_name="Juan",
              last_name="Dela Cruz", assignee_type="DRIVER",
              branch_id=branch.id, user_id=user.id if user else None)
    db.session.add(d)
    db.session.commit()
    return d


def _user(username="jdelacruz"):
    u = User(username=username, email=f"{username}@example.com",
            password_hash=hash_password("secret123"), is_active=True)
    db.session.add(u)
    db.session.commit()
    return u


# ── Core destructive behavior ────────────────────────────────────────────

def test_wipes_vehicles_vendors_drivers_and_their_linked_user(db, env):
    vt, branch = env
    _vehicle(vt, branch)
    _vendor()
    user = _user()
    _driver(branch, user=user)

    assert Vehicle.query.count() == 1
    assert Vendor.query.count() == 1
    assert Driver.query.count() == 1
    assert User.query.filter_by(username="jdelacruz").first() is not None

    _reset_uat_baseline()

    assert Vehicle.query.count() == 0
    assert Vendor.query.count() == 0
    assert Driver.query.count() == 0
    assert User.query.filter_by(username="jdelacruz").first() is None


def test_wipes_dependent_child_tables(db, env):
    vt, branch = env
    vehicle = _vehicle(vt, branch)
    vendor = _vendor()
    driver = _driver(branch)

    db.session.add(Tire(serial_number="TIRE-1", brand="Bridgestone",
                        size="195/70", tire_type="RADIAL",
                        vendor_id=vendor.id, vehicle_id=vehicle.id))
    db.session.add(Battery(serial_number="BATT-1", brand="Motolite",
                           vendor_id=vendor.id, vehicle_id=vehicle.id))
    db.session.add(VendorContact(vendor_id=vendor.id, contact_name="Rep"))
    db.session.add(EmergencyContact(person_record_id=driver.id,
                                    contact_name="Spouse"))
    db.session.add(VehicleAssignment(vehicle_id=vehicle.id,
                                     driver_id=driver.id,
                                     assigned_from=date.today()))
    db.session.add(VehicleOdometerLog(vehicle_id=vehicle.id, reading=1000,
                                      recorded_at=date.today()))
    vendor.branches.append(branch)
    db.session.commit()
    assert db.session.execute(vendor_branches.select()).fetchall()

    handover = VehicleHandoverChecklist(vehicle_id=vehicle.id)
    db.session.add(handover)
    db.session.flush()
    db.session.add(HandoverItem(handover_id=handover.id, item_name="Keys"))
    db.session.commit()

    template = ChecklistTemplate(name="Daily", status="ACTIVE")
    db.session.add(template)
    db.session.flush()
    db.session.add(VehicleChecklist(template_id=template.id,
                                    vehicle_id=vehicle.id,
                                    inspection_date=date.today()))
    db.session.commit()

    card = FuelCard(card_number="CARD-1", vehicle_id=vehicle.id,
                    driver_id=driver.id, vendor_id=vendor.id)
    db.session.add(card)
    db.session.flush()
    db.session.add(FuelTransaction(
        vehicle_id=vehicle.id, driver_id=driver.id, fuel_card_id=card.id,
        vendor_id=vendor.id, transaction_date=date.today(),
        litres=10, total_amount=500))
    db.session.commit()

    _reset_uat_baseline()

    assert Tire.query.count() == 0
    assert Battery.query.count() == 0
    assert VendorContact.query.count() == 0
    assert EmergencyContact.query.count() == 0
    assert VehicleAssignment.query.count() == 0
    assert VehicleOdometerLog.query.count() == 0
    assert db.session.execute(vendor_branches.select()).fetchall() == []
    assert VehicleHandoverChecklist.query.count() == 0
    assert HandoverItem.query.count() == 0
    assert VehicleChecklist.query.count() == 0
    assert FuelCard.query.count() == 0
    assert FuelTransaction.query.count() == 0
    # The template itself is CONFIGURATION, not an instance -- kept.
    assert ChecklistTemplate.query.count() == 1


def test_reuses_reset_transactions_for_transactional_tables(db, env):
    """The new command must call the EXISTING `_reset_transactions()`
    rather than duplicating its table list."""
    vt, branch = env
    mt = MaintenanceTypeService().create(code="PMS-U", name="PMS",
                                         category="PREVENTIVE")
    vehicle = _vehicle(vt, branch)
    MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)

    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    assert MaintenanceOrder.query.count() == 1

    _reset_uat_baseline()

    assert MaintenanceOrder.query.count() == 0


# ── What survives untouched ──────────────────────────────────────────────

def test_configuration_and_non_driver_users_survive(db, env):
    vt, branch = env
    role = Role(name="Fleet Admin", description="admin")
    db.session.add(role)
    db.session.flush()
    perm = Permission(code="driver.view", module="driver", action="view")
    db.session.add(perm)
    role.permissions.append(perm)
    admin_user = _user("fleetadmin")
    admin_user.roles.append(role)
    db.session.commit()

    assert Branch.query.count() == 1

    _reset_uat_baseline()

    assert Branch.query.filter_by(code="MNL").first() is not None
    assert Role.query.filter_by(name="Fleet Admin").first() is not None
    assert Permission.query.filter_by(code="driver.view").first() is not None
    assert User.query.filter_by(username="fleetadmin").first() is not None


def test_blocks_cleanly_when_driver_user_is_referenced_by_approval_config(
        db, env):
    """A driver-linked user still named by a SPECIFIC-user approval
    level must block deletion with a clean, named error and a full
    rollback -- not a raw FK error, and not a partial delete."""
    vt, branch = env
    vehicle = _vehicle(vt, branch)
    user = _user()
    driver = _driver(branch, user=user)

    path = ApprovalPath(name="Special Path")
    db.session.add(path)
    db.session.flush()
    db.session.add(ApprovalLevel(path_id=path.id, level_number=1,
                                 approver_type="USER", user_id=user.id))
    db.session.commit()

    with pytest.raises(RuntimeError, match="approval_levels"):
        _reset_uat_baseline()

    # Nothing was partially deleted -- not even the transactions step.
    assert Vehicle.query.count() == 1
    assert Driver.query.count() == 1
    assert User.query.filter_by(username="jdelacruz").first() is not None


def test_blocks_cleanly_when_driver_user_is_a_notification_rule_recipient(
        db, env):
    vt, branch = env
    user = _user()
    _driver(branch, user=user)

    db.session.add(NotificationRule(
        event_code="MO_SUBMITTED", channel="IN_APP",
        recipient_type="SPECIFIC_USER", user_id=user.id))
    db.session.commit()

    with pytest.raises(RuntimeError, match="notification_rules"):
        _reset_uat_baseline()

    assert Driver.query.count() == 1
    assert User.query.filter_by(username="jdelacruz").first() is not None


def test_non_driver_linked_user_never_blocks_or_deletes(db, env):
    """An ordinary staff/admin login (no Driver record points at it)
    must survive completely untouched, even if nothing else changes."""
    vt, branch = env
    _user("staffuser")
    _reset_uat_baseline()
    assert User.query.filter_by(username="staffuser").first() is not None
