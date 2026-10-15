"""Tire & battery foundation (2026-10-15 decisions, Phase 1).

1. Brands, sizes, battery models and wheel positions are admin-maintained
   Lookups -- the dropdowns read them, nothing is hardcoded.
2. Tread / battery thresholds are System Parameters.
3. MOUNT puts the tire (or battery) ON the vehicle -- master.vehicle_id and
   wheel_position -- and DISMOUNT / DISPOSE take it off. Before this, a
   mount changed only the status, so "which tire is on which wheel" could
   not be answered from transactions.
4. New non-moving actions: ROTATE (tire changes wheel), INSPECT / REPAIR
   (tire, reading only), TEST (battery, reading only).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService)
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-FD", name="FD Branch")
    vt = VehicleTypeService().create(code="LV-FD", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="FD-000")
    tire = TireService().create(serial_number="T-FD1", brand="Bridgestone",
                                size="185/60 R15", tire_type="RADIAL")
    battery = BatteryService().create(serial_number="B-FD1", brand="Motolite")
    user = User(username="fd_user", email="fd@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    return vehicle, tire, battery, user


# ── 1. Lookups ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("lookup_type, code", [
    ("TIRE_BRAND", "BRIDGESTONE"),
    ("TIRE_BRAND", "GOODYEAR"),
    ("TIRE_SIZE", "185/60 R15"),
    ("BATTERY_BRAND", "MOTOLITE"),
    ("BATTERY_MODEL", "NS40ZL"),
    ("WHEEL_POSITION", "FL"),
    ("WHEEL_POSITION", "SPARE"),
])
def test_tire_battery_lookups_are_registered(app, lookup_type, code):
    import app.modules.master_data.routes  # noqa: F401  (registers)
    from app.modules.system_admin.services.lookup_service import registry
    codes = {d.code for d in registry.definitions
             if d.lookup_type == lookup_type}
    assert code in codes


def test_wheel_positions_are_ordered_front_to_back(app):
    import app.modules.master_data.routes  # noqa: F401
    from app.modules.system_admin.services.lookup_service import registry
    order = [d.code for d in sorted(
        (d for d in registry.definitions if d.lookup_type == "WHEEL_POSITION"),
        key=lambda d: d.sort_order)]
    assert order[:4] == ["FL", "FR", "RL", "RR"]
    assert order[-1] == "SPARE"


# ── 2. System parameters ───────────────────────────────────────────────

def test_tire_battery_thresholds_are_seeded(db):
    from app.cli import _seed_system_parameters
    from app.modules.system_admin.models import SystemParameter
    _seed_system_parameters()
    db.session.commit()
    got = {p.code: p.value for p in SystemParameter.query.filter(
        SystemParameter.code.in_([
            "TIRE_TREAD_WATCH_MM", "TIRE_TREAD_REPLACE_MM",
            "TIRE_MAX_AGE_MONTHS", "BATTERY_LOW_VOLTAGE",
            "BATTERY_WARRANTY_WARN_DAYS"])).all()}
    assert got == {"TIRE_TREAD_WATCH_MM": "4.0",
                   "TIRE_TREAD_REPLACE_MM": "3.0",
                   "TIRE_MAX_AGE_MONTHS": "60",
                   "BATTERY_LOW_VOLTAGE": "12.4",
                   "BATTERY_WARRANTY_WARN_DAYS": "30"}


# ── 3. Mount puts it ON the vehicle ────────────────────────────────────

def test_tire_mount_records_vehicle_and_wheel(db, env):
    vehicle, tire, _, user = env
    TireTransactionService().create(
        tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
        wheel_position="FL", transaction_date=date(2026, 10, 1),
        odometer_at_service=27000, user=user)
    t = TireService().get(tire.id)
    assert (t.status, t.vehicle_id, t.wheel_position) == (
        "MOUNTED", vehicle.id, "FL")
    assert t.mounted_date == date(2026, 10, 1)
    assert t.mounted_odometer == 27000


def test_tire_rotate_requires_a_vehicle(db, env):
    _, tire, _, user = env
    from app.modules.transactions.tire_txn.service import (
        InvalidTireActionError)
    with pytest.raises(InvalidTireActionError):
        TireTransactionService().create(
            tire_id=tire.id, vehicle_id=None, action="ROTATE",
            wheel_position="FL", transaction_date=date(2026, 10, 1),
            user=user)


def test_tire_mount_without_vehicle_still_accepted(db, env):
    """Backward compatibility: the existing screen allows it."""
    _, tire, _, user = env
    TireTransactionService().create(
        tire_id=tire.id, vehicle_id=None, action="MOUNT",
        transaction_date=date(2026, 10, 1), user=user)
    assert TireService().get(tire.id).status == "MOUNTED"


@pytest.mark.parametrize("action, status", [
    ("DISMOUNT", "IN_STOCK"), ("DISPOSE", "DISPOSED"),
    ("RETREAD", "RETREADED")])
def test_tire_off_actions_take_it_off_the_vehicle(db, env, action, status):
    vehicle, tire, _, user = env
    svc = TireTransactionService()
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
               wheel_position="RR", transaction_date=date(2026, 10, 1),
               user=user)
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action=action,
               transaction_date=date(2026, 10, 2), user=user)
    t = TireService().get(tire.id)
    assert (t.status, t.vehicle_id, t.wheel_position) == (status, None, None)


def test_tire_rotate_moves_wheel_only(db, env):
    vehicle, tire, _, user = env
    svc = TireTransactionService()
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
               wheel_position="FL", transaction_date=date(2026, 10, 1),
               user=user)
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="ROTATE",
               wheel_position="RL", transaction_date=date(2026, 10, 2),
               user=user)
    t = TireService().get(tire.id)
    assert (t.status, t.vehicle_id, t.wheel_position) == (
        "MOUNTED", vehicle.id, "RL")


@pytest.mark.parametrize("action", ["INSPECT", "REPAIR"])
def test_tire_reading_actions_record_tread_without_moving(db, env, action):
    vehicle, tire, _, user = env
    svc = TireTransactionService()
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
               wheel_position="FR", transaction_date=date(2026, 10, 1),
               user=user)
    txn = svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action=action,
                     wheel_position="FR", tread_depth_mm="5.5",
                     pressure_psi=33, transaction_date=date(2026, 10, 2),
                     user=user)
    t = TireService().get(tire.id)
    assert (t.status, t.vehicle_id, t.wheel_position) == (
        "MOUNTED", vehicle.id, "FR")
    assert t.current_tread_mm == Decimal("5.5")
    assert txn.tread_depth_mm == Decimal("5.5")
    assert txn.pressure_psi == 33


def test_tire_txn_defaults_to_manual_source_with_no_mo(db, env):
    vehicle, tire, _, user = env
    txn = TireTransactionService().create(
        tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
        wheel_position="FL", transaction_date=date(2026, 10, 1), user=user)
    assert txn.source == "MANUAL"
    assert txn.maintenance_order_id is None


def test_battery_mount_and_dismount_track_the_vehicle(db, env):
    vehicle, _, battery, user = env
    svc = BatteryTransactionService()
    svc.create(battery_id=battery.id, vehicle_id=vehicle.id, action="MOUNT",
               transaction_date=date(2026, 10, 1), odometer_at_service=27000,
               user=user)
    b = BatteryService().get(battery.id)
    assert (b.status, b.vehicle_id) == ("MOUNTED", vehicle.id)
    assert b.mounted_date == date(2026, 10, 1)
    svc.create(battery_id=battery.id, vehicle_id=vehicle.id,
               action="DISMOUNT", transaction_date=date(2026, 10, 5),
               user=user)
    b = BatteryService().get(battery.id)
    assert (b.status, b.vehicle_id) == ("IN_STOCK", None)


def test_battery_test_records_voltage_without_moving(db, env):
    vehicle, _, battery, user = env
    svc = BatteryTransactionService()
    svc.create(battery_id=battery.id, vehicle_id=vehicle.id, action="MOUNT",
               transaction_date=date(2026, 10, 1), user=user)
    txn = svc.create(battery_id=battery.id, vehicle_id=vehicle.id,
                     action="TEST", voltage="12.3", test_result="WEAK",
                     transaction_date=date(2026, 10, 3), user=user)
    b = BatteryService().get(battery.id)
    assert (b.status, b.vehicle_id) == ("MOUNTED", vehicle.id)
    assert b.last_voltage == Decimal("12.3")
    assert b.last_test_date == date(2026, 10, 3)
    assert txn.test_result == "WEAK"
    assert txn.source == "MANUAL"


def test_battery_rejects_unknown_test_result(db, env):
    vehicle, _, battery, user = env
    from app.modules.transactions.battery_txn.service import (
        InvalidBatteryActionError)
    with pytest.raises(InvalidBatteryActionError):
        BatteryTransactionService().create(
            battery_id=battery.id, vehicle_id=vehicle.id, action="TEST",
            test_result="MAYBE", transaction_date=date(2026, 10, 1),
            user=user)


def test_physical_effect_waits_for_approval(db, env):
    """Unchanged rule: when TIR requires approval, a draft moves nothing."""
    vehicle, tire, _, user = env
    from app.modules.document_config.service import DocumentTypeService
    DocumentTypeService().create(code="TIR", name="Tire Transaction",
                                 requires_approval=True, auto_numbering=False)
    txn = TireTransactionService().create(
        tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
        wheel_position="FL", transaction_date=date(2026, 10, 1), user=user)
    t = TireService().get(tire.id)
    assert txn.status == "DRAFT"
    assert (t.status, t.vehicle_id, t.wheel_position) == (
        "IN_STOCK", None, None)
