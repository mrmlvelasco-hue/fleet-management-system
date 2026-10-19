"""Standalone tire / battery transaction as one work document
(mockup "New tire transaction", 2026-10-15; reported missing 2026-10-19).

A flat on the road is ONE document: which wheel, the tire that came off
(and where it went), the tire that went on (from stock, or new from the
workshop -- registered when the document completes), readings, the scope
of work, workshop, technician, cost, and optionally the open MO it belongs
to. It follows the TIR / BAT approval setting: nothing physical happens
until it completes.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db as _db
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService, InvalidBatteryActionError)
from app.modules.transactions.tire_txn.service import (
    InvalidTireActionError, TireTransactionService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-WT", name="Muntinlupa")
    vt = VehicleTypeService().create(code="LV-WT", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=branch.id, conduction_number="WT-1", plate_number="NZO-619")
    vendor = VendorService().create(code="VEN-WT", name="Gulf Tire Center")
    fr = TireService().create(serial_number="BS-FR", brand="Bridgestone",
                              size="185/60 R15", tire_type="RADIAL")
    fr.status, fr.vehicle_id, fr.wheel_position = "MOUNTED", vehicle.id, "FR"
    stock = TireService().create(serial_number="BS-STOCK", brand="Bridgestone",
                                 size="185/60 R15", tire_type="RADIAL")
    batt = BatteryService().create(serial_number="MT-OLD", brand="Motolite")
    batt.status, batt.vehicle_id = "MOUNTED", vehicle.id
    user = User(username="wt_user", email="wt@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    return dict(vehicle=vehicle, vendor=vendor, fr=fr, stock=stock,
                batt=batt, user=user)


def _approval(required):
    for code, name in [("TIR", "Tire Transaction"),
                       ("BAT", "Battery Transaction")]:
        dt = DocumentTypeService().create(code=code, name=name,
                                          requires_approval=required,
                                          auto_numbering=True)
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


def _replace_fr(env, **kw):
    args = dict(action="REPLACE", vehicle_id=env["vehicle"].id,
                wheel_position="FR", transaction_date=date(2026, 10, 15),
                odometer_at_service=45410, user=env["user"],
                removal_reason="SIDEWALL", removed_to="FOR_DISPOSAL",
                install_source="VENDOR", new_serial="BS-26-00415",
                new_brand="Bridgestone", new_model="Ecopia EP150",
                new_size="185/60 R15", tread_depth_mm="8.0", pressure_psi=33,
                vendor_id=env["vendor"].id, technician="R. Dizon",
                cost="4850", labor_cost="350",
                scope_items=["Dismount and inspect wheel", "Wheel balancing"],
                remarks="Sidewall cut on SLEX")
    args.update(kw)
    return TireTransactionService().create(**args)


def test_replace_without_approval_swaps_in_one_document(db, env):
    _approval(False)
    txn = _replace_fr(env)
    assert txn.status == "COMPLETED"
    old = _db.session.get(Tire, env["fr"].id)
    assert (old.status, old.vehicle_id, old.wheel_position) == (
        "IN_STOCK", None, None)
    new = Tire.query.filter_by(serial_number="BS-26-00415").one()
    assert (new.status, new.vehicle_id, new.wheel_position) == (
        "MOUNTED", env["vehicle"].id, "FR")
    assert (new.brand, new.model, new.size) == (
        "Bridgestone", "Ecopia EP150", "185/60 R15")
    assert new.vendor_id == env["vendor"].id
    assert new.current_tread_mm == Decimal("8.0")
    # One document carries both sides.
    assert txn.tire_id == new.id
    assert txn.removed_tire_id == old.id
    assert txn.removed_to == "FOR_DISPOSAL"
    assert txn.removal_reason == "SIDEWALL"
    assert (txn.technician, txn.vendor_id) == ("R. Dizon", env["vendor"].id)
    assert (txn.cost, txn.labor_cost) == (Decimal("4850"), Decimal("350"))
    assert [i.activity_description for i in txn.scope_items] == [
        "Dismount and inspect wheel", "Wheel balancing"]


def test_replace_finds_the_tire_on_that_wheel(db, env):
    _approval(False)
    txn = _replace_fr(env)
    assert txn.removed_tire_id == env["fr"].id


def test_replace_with_approval_moves_nothing_until_approved(db, env):
    _approval(True)
    txn = _replace_fr(env)
    assert txn.status == "DRAFT"
    assert txn.tire_id is None                  # not registered yet
    fr = _db.session.get(Tire, env["fr"].id)
    assert (fr.status, fr.wheel_position) == ("MOUNTED", "FR")
    assert Tire.query.filter_by(serial_number="BS-26-00415").first() is None


def test_replace_from_stock(db, env):
    _approval(False)
    _replace_fr(env, install_source="STOCK", tire_id=env["stock"].id,
                new_serial=None, new_brand=None, new_size=None)
    st = _db.session.get(Tire, env["stock"].id)
    assert (st.status, st.vehicle_id, st.wheel_position) == (
        "MOUNTED", env["vehicle"].id, "FR")


@pytest.mark.parametrize("kw, field", [
    (dict(vehicle_id=None), "vehicle_id"),
    (dict(wheel_position=None), "wheel_position"),
    (dict(install_source=None), "install_source"),
    (dict(new_brand=None), "new_brand"),
    (dict(new_size=None), "new_size"),
    (dict(new_serial="BS-STOCK"), "new_serial"),
    (dict(install_source="STOCK", tire_id=None), "tire_id"),
    (dict(removed_to="BIN"), "removed_to"),
])
def test_replace_validation_names_the_field(db, env, kw, field):
    _approval(False)
    with pytest.raises(InvalidTireActionError) as e:
        _replace_fr(env, **kw)
    assert e.value.field == field


def test_stock_tire_must_be_in_stock(db, env):
    _approval(False)
    with pytest.raises(InvalidTireActionError) as e:
        _replace_fr(env, install_source="STOCK", tire_id=env["fr"].id)
    assert e.value.field == "tire_id"


def test_linked_mo_must_be_open_and_same_vehicle(db, env):
    _approval(False)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    mt = MaintenanceTypeService().create(code="CM-WT", name="Repair",
                                         category="CM")
    other = VehicleService().create(
        vehicle_type_id=env["vehicle"].vehicle_type_id, brand="Toyota",
        model="Vios", year=2021, branch_id=env["vehicle"].branch_id,
        conduction_number="WT-2")
    mo_other = MaintenanceOrderService().create(
        vehicle_id=other.id, scheduled_date=date(2026, 10, 15),
        user=env["user"], maintenance_type_id=mt.id)
    with pytest.raises(InvalidTireActionError) as e:
        _replace_fr(env, maintenance_order_id=mo_other.id)
    assert e.value.field == "maintenance_order_id"
    mo = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, scheduled_date=date(2026, 10, 15),
        user=env["user"], maintenance_type_id=mt.id)
    txn = _replace_fr(env, maintenance_order_id=mo.id)
    assert txn.maintenance_order_id == mo.id


def test_old_actions_still_work_unchanged(db, env):
    """Backward compatibility: a plain MOUNT by tire id."""
    _approval(False)
    txn = TireTransactionService().create(
        tire_id=env["stock"].id, action="MOUNT", vehicle_id=env["vehicle"].id,
        wheel_position="SPARE", transaction_date=date(2026, 10, 15),
        user=env["user"])
    assert txn.status == "COMPLETED"
    assert _db.session.get(Tire, env["stock"].id).wheel_position == "SPARE"


def test_non_replace_actions_still_need_a_tire(db, env):
    _approval(False)
    with pytest.raises(InvalidTireActionError) as e:
        TireTransactionService().create(
            tire_id=None, action="INSPECT", vehicle_id=env["vehicle"].id,
            transaction_date=date(2026, 10, 15), user=env["user"])
    assert e.value.field == "tire_id"


# ── Battery ────────────────────────────────────────────────────────────

def _replace_battery(env, **kw):
    args = dict(action="REPLACE", vehicle_id=env["vehicle"].id,
                transaction_date=date(2026, 10, 15), user=env["user"],
                removed_to="FOR_DISPOSAL", removal_reason="DEAD",
                install_source="VENDOR", new_serial="MT-NEW",
                new_brand="Motolite", new_model="NS40ZL",
                new_warranty_months=18, voltage="12.7", test_result="GOOD",
                vendor_id=env["vendor"].id, cost="5200",
                scope_items=["Install and test charging"])
    args.update(kw)
    return BatteryTransactionService().create(**args)


def test_battery_replace_in_one_document(db, env):
    _approval(False)
    txn = _replace_battery(env)
    old = _db.session.get(Battery, env["batt"].id)
    assert (old.status, old.vehicle_id) == ("IN_STOCK", None)
    new = Battery.query.filter_by(serial_number="MT-NEW").one()
    assert (new.status, new.vehicle_id, new.model, new.warranty_months) == (
        "MOUNTED", env["vehicle"].id, "NS40ZL", 18)
    assert new.last_voltage == Decimal("12.7")
    assert txn.battery_id == new.id
    assert txn.removed_battery_id == old.id
    assert [i.activity_description for i in txn.scope_items] == [
        "Install and test charging"]


def test_battery_replace_needs_brand(db, env):
    _approval(False)
    with pytest.raises(InvalidBatteryActionError) as e:
        _replace_battery(env, new_brand=None)
    assert e.value.field == "new_brand"


def test_battery_replace_with_approval_waits(db, env):
    _approval(True)
    txn = _replace_battery(env)
    assert txn.status == "DRAFT" and txn.battery_id is None
    assert _db.session.get(Battery, env["batt"].id).status == "MOUNTED"


def test_replace_completes_on_final_approval(db, env):
    from app.modules.approval_config.service import (
        ApprovalMatrixService, ApprovalPathService)
    from app.modules.user_management.models import Role
    role = Role(name="WT approver")
    approver = User(username="wt_appr", email="wa@x.com", password_hash="x")
    approver.roles.append(role)
    db.session.add_all([role, approver])
    db.session.commit()
    dt = DocumentTypeService().create(code="TIR", name="Tire Transaction",
                                      requires_approval=True,
                                      auto_numbering=False)
    path = ApprovalPathService().create(name="TIR Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None,
                                   max_amount=None)
    svc = TireTransactionService()
    txn = _replace_fr(env)
    svc.submit(txn.id, user=env["user"])
    svc.approve(txn.id, user=approver)
    txn = svc.get(txn.id)
    assert txn.status == "COMPLETED"
    new = Tire.query.filter_by(serial_number="BS-26-00415").one()
    assert (new.status, new.wheel_position) == ("MOUNTED", "FR")
    assert txn.tire_id == new.id
    assert _db.session.get(Tire, env["fr"].id).status == "IN_STOCK"


# Mockup battery actions: Inspect · Test · Charge · Replace · Dispose.
# Inspect and Charge, like Test, only record the reading.
@pytest.mark.parametrize("action", ["INSPECT", "CHARGE", "TEST"])
def test_battery_reading_actions_do_not_move_it(db, env, action):
    _approval(False)
    BatteryTransactionService().create(
        battery_id=env["batt"].id, action=action,
        transaction_date=date(2026, 10, 19), user=env["user"],
        vehicle_id=env["vehicle"].id, voltage="12.5")
    b = _db.session.get(Battery, env["batt"].id)
    assert (b.status, b.vehicle_id) == ("MOUNTED", env["vehicle"].id)
    assert b.last_voltage == Decimal("12.5")
    assert b.last_test_date == date(2026, 10, 19)
