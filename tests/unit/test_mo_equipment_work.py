"""Maintenance Order "Tire & Battery Work" (client decisions 2026-10-15).

- Shown for maintenance types whose equipment_kind is TIRE / BATTERY
  (PMS-TIRE / PMS-BATT); suggested lines list what is on the vehicle now.
- A line is PLANNED until the mechanic records actuals (DONE). The MO
  cannot be completed while a line is still PLANNED.
- Completing the MO posts the tire / battery transactions: COMPLETED, no
  approval (even when TIR/BAT require approval), linked to the MO,
  source MO. Vendor-supplied new tires / batteries are registered
  automatically; a tire that was never enrolled ("not registered") gets a
  master record so its history starts here.
- Posting happens once: completing again never doubles the transactions.
- Draft / cancelled orders move nothing.
- No cost on the lines; MO-sourced transactions carry no cost.
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
from app.modules.transactions.battery_txn.models import BatteryTransaction
from app.modules.transactions.maintenance_order.equipment import (
    EquipmentWorkError, EquipmentWorkService)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-EQ", name="Muntinlupa")
    vt = VehicleTypeService().create(code="LV-EQ", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=branch.id, conduction_number="EQ-001",
        plate_number="TIL-120")
    vendor = VendorService().create(code="VEN-EQ", name="Gulf Tire Center")
    tire_type = MaintenanceTypeService().create(
        code="PMS-TIRE", name="Tire Replacement", category="PREVENTIVE",
        equipment_kind="TIRE")
    batt_type = MaintenanceTypeService().create(
        code="PMS-BATT", name="Battery Replacement", category="PREVENTIVE",
        equipment_kind="BATTERY")
    prev_type = MaintenanceTypeService().create(
        code="PMS-PREV", name="Preventive Maintenance", category="PREVENTIVE")
    user = User(username="eq_user", email="eq@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()
    for code, name in [("TIR", "Tire Transaction"),
                       ("BAT", "Battery Transaction")]:
        dt = DocumentTypeService().create(code=code, name=name,
                                          requires_approval=True,
                                          auto_numbering=True)
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    # Two tires already on the vehicle (FL, FR); RL / RR never enrolled.
    tires = {}
    for pos, serial in [("FL", "BS-24-10931"), ("FR", "BS-24-10932")]:
        t = TireService().create(serial_number=serial, brand="Bridgestone",
                                 size="185/60 R15", tire_type="RADIAL")
        t.status, t.vehicle_id, t.wheel_position = "MOUNTED", vehicle.id, pos
        t.current_tread_mm = Decimal("3.1")
        tires[pos] = t
    battery = BatteryService().create(serial_number="MT-25-0412",
                                      brand="Motolite")
    battery.status, battery.vehicle_id = "MOUNTED", vehicle.id
    stock_tire = TireService().create(serial_number="BS-STOCK-1",
                                      brand="Bridgestone", size="185/60 R15",
                                      tire_type="RADIAL")
    db.session.commit()
    return dict(vehicle=vehicle, vendor=vendor, user=user, tires=tires,
                battery=battery, stock_tire=stock_tire, tire_type=tire_type,
                batt_type=batt_type, prev_type=prev_type)


def _mo(env, mtype, **kw):
    return MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, scheduled_date=date(2026, 10, 15),
        user=env["user"], maintenance_type_id=env[mtype].id,
        vendor_id=env["vendor"].id, odometer_at_service=27000, **kw)


def _complete(mo, env):
    return MaintenanceOrderService().complete(
        mo.id, actual_cost=0, completed_date=date(2026, 10, 16),
        user=env["user"])


# ── Suggestions ────────────────────────────────────────────────────────

def test_prefill_lists_what_is_on_the_vehicle_for_a_tire_mo(db, env):
    out = EquipmentWorkService().prefill(env["vehicle"].id,
                                         env["tire_type"].id)
    assert out["kind"] == "TIRE"
    by_pos = {l["position"]: l for l in out["lines"]}
    assert by_pos["FL"]["current_tire_id"] == env["tires"]["FL"].id
    assert by_pos["FL"]["current_label"].startswith("BS-24-10931")
    # Not-registered positions still appear so they can be baselined.
    assert by_pos["RL"]["current_tire_id"] is None
    assert by_pos["RL"]["not_registered"] is True
    assert list(by_pos)[:4] == ["FL", "FR", "RL", "RR"]
    assert all(l["action"] == "REPLACE" and l["status"] == "PLANNED"
               for l in out["lines"])


def test_prefill_for_a_battery_mo_has_one_line(db, env):
    out = EquipmentWorkService().prefill(env["vehicle"].id,
                                         env["batt_type"].id)
    assert out["kind"] == "BATTERY"
    assert [l["current_battery_id"] for l in out["lines"]] == [
        env["battery"].id]


def test_prefill_is_empty_for_other_maintenance_types(db, env):
    out = EquipmentWorkService().prefill(env["vehicle"].id,
                                         env["prev_type"].id)
    assert out == {"kind": None, "lines": [],
                   "thresholds": out["thresholds"]}


def test_legacy_type_codes_still_detected_without_equipment_kind(db, env):
    """Rows imported before equipment_kind existed: PMS-TIRE / PMS-BATT."""
    env["tire_type"].equipment_kind = None
    db.session.commit()
    assert EquipmentWorkService().kind_for_type(env["tire_type"]) == "TIRE"


def test_prefill_carries_condition_from_system_parameters(db, env):
    from app.modules.system_admin.models import SystemParameter
    db.session.add(SystemParameter(code="TIRE_TREAD_REPLACE_MM", value="3.5",
                                   data_type="DECIMAL",
                                   group_name="TIRE_BATTERY"))
    db.session.commit()
    out = EquipmentWorkService().prefill(env["vehicle"].id,
                                         env["tire_type"].id)
    fl = next(l for l in out["lines"] if l["position"] == "FL")
    assert fl["condition"] == "REPLACE"      # 3.1 <= 3.5
    assert out["thresholds"]["tire_replace_mm"] == 3.5


# ── Saving lines ───────────────────────────────────────────────────────

def test_create_mo_with_lines_saves_them_planned(db, env):
    mo = _mo(env, "tire_type", equipment_lines=[
        {"kind": "TIRE", "position": "FL", "action": "REPLACE",
         "current_tire_id": env["tires"]["FL"].id}])
    lines = EquipmentWorkService().lines(mo.id)
    assert [(l.position, l.action, l.status) for l in lines] == [
        ("FL", "REPLACE", "PLANNED")]


def test_lines_rejected_on_operational_order(db, env):
    from app.cli import _seed_transaction_types
    _seed_transaction_types()
    db.session.commit()
    from app.modules.transactions.maintenance_order.models import (
        TransactionType)
    tt = TransactionType.query.filter_by(order_category="OPERATIONAL").first()
    mo = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, scheduled_date=date(2026, 10, 15),
        user=env["user"], order_category="OPERATIONAL",
        transaction_type_id=tt.id)
    with pytest.raises(EquipmentWorkError):
        EquipmentWorkService().replace_lines(mo.id, [
            {"kind": "TIRE", "position": "FL", "action": "INSPECT"}])


@pytest.mark.parametrize("row, field", [
    ({"kind": "WHEEL", "position": "FL", "action": "REPLACE"}, "kind"),
    ({"kind": "TIRE", "position": "", "action": "REPLACE"}, "position"),
    ({"kind": "TIRE", "position": "FL", "action": "TEST"}, "action"),
    ({"kind": "BATTERY", "action": "ROTATE"}, "action"),
    ({"kind": "TIRE", "position": "FL", "action": "ROTATE",
      "current_tire_id": 1, "status": "DONE"}, "rotate_to_position"),
])
def test_invalid_lines_name_the_field(db, env, row, field):
    mo = _mo(env, "tire_type")
    with pytest.raises(EquipmentWorkError) as e:
        EquipmentWorkService().replace_lines(mo.id, [row])
    assert e.value.field == field


def test_done_replace_needs_what_was_installed(db, env):
    mo = _mo(env, "tire_type")
    with pytest.raises(EquipmentWorkError) as e:
        EquipmentWorkService().replace_lines(mo.id, [
            {"kind": "TIRE", "position": "FL", "action": "REPLACE",
             "current_tire_id": env["tires"]["FL"].id, "status": "DONE",
             "install_source": "VENDOR"}])
    assert e.value.field == "new_brand"


def test_stock_tire_must_be_in_stock(db, env):
    mo = _mo(env, "tire_type")
    with pytest.raises(EquipmentWorkError) as e:
        EquipmentWorkService().replace_lines(mo.id, [
            {"kind": "TIRE", "position": "FL", "action": "REPLACE",
             "current_tire_id": env["tires"]["FL"].id, "status": "DONE",
             "install_source": "STOCK",
             "installed_tire_id": env["tires"]["FR"].id}])
    assert e.value.field == "installed_tire_id"


def test_vendor_serial_already_registered_is_refused(db, env):
    mo = _mo(env, "tire_type")
    with pytest.raises(EquipmentWorkError) as e:
        EquipmentWorkService().replace_lines(mo.id, [
            {"kind": "TIRE", "position": "FL", "action": "REPLACE",
             "current_tire_id": env["tires"]["FL"].id, "status": "DONE",
             "install_source": "VENDOR", "new_serial": "BS-STOCK-1",
             "new_brand": "Bridgestone", "new_size": "185/60 R15"}])
    assert e.value.field == "new_serial"


def test_lines_locked_after_completion(db, env):
    mo = _mo(env, "tire_type")
    _complete(mo, env)
    with pytest.raises(EquipmentWorkError):
        EquipmentWorkService().replace_lines(mo.id, [])


# ── Completion ─────────────────────────────────────────────────────────

def test_complete_blocked_while_a_line_is_planned(db, env):
    from app.modules.transactions.maintenance_order.service import (
        IncompleteEquipmentWorkError)
    mo = _mo(env, "tire_type", equipment_lines=[
        {"kind": "TIRE", "position": "FL", "action": "REPLACE",
         "current_tire_id": env["tires"]["FL"].id}])
    with pytest.raises(IncompleteEquipmentWorkError):
        _complete(mo, env)
    assert _db.session.get(type(mo), mo.id).status != "COMPLETED"
    assert TireTransaction.query.count() == 0


def _replace_fl_from_vendor(env, **extra):
    row = {"kind": "TIRE", "position": "FL", "action": "REPLACE",
           "current_tire_id": env["tires"]["FL"].id, "status": "DONE",
           "install_source": "VENDOR", "new_serial": "DOT-4126-00412",
           "new_brand": "Bridgestone", "new_model": "Ecopia EP150",
           "new_size": "185/60 R15", "tread_depth_mm": "8.0",
           "pressure_psi": 33}
    row.update(extra)
    return row


def test_complete_replace_registers_vendor_tire_and_swaps(db, env):
    mo = _mo(env, "tire_type",
             equipment_lines=[_replace_fl_from_vendor(env)])
    _complete(mo, env)

    old = _db.session.get(Tire, env["tires"]["FL"].id)
    assert (old.status, old.vehicle_id, old.wheel_position) == (
        "IN_STOCK", None, None)
    new = Tire.query.filter_by(serial_number="DOT-4126-00412").one()
    assert (new.status, new.vehicle_id, new.wheel_position) == (
        "MOUNTED", env["vehicle"].id, "FL")
    assert (new.brand, new.model, new.size) == (
        "Bridgestone", "Ecopia EP150", "185/60 R15")
    assert new.vendor_id == env["vendor"].id
    assert new.branch_id == env["vehicle"].branch_id
    assert new.purchase_date == date(2026, 10, 16)
    assert new.mounted_odometer == 27000
    assert new.current_tread_mm == Decimal("8.0")

    txns = TireTransaction.query.order_by(TireTransaction.id).all()
    assert [(t.action, t.tire_id) for t in txns] == [
        ("DISMOUNT", old.id), ("MOUNT", new.id)]
    for t in txns:
        assert t.status == "COMPLETED"          # no approval
        assert t.approval_instance_id is None
        assert t.source == "MO"
        assert t.maintenance_order_id == mo.id
        assert t.vehicle_id == env["vehicle"].id
        assert t.transaction_date == date(2026, 10, 16)
        assert t.odometer_at_service == 27000
        assert t.cost is None                   # MO is the cost
        assert t.document_number.startswith("TIR-")


def test_complete_replace_from_stock(db, env):
    mo = _mo(env, "tire_type", equipment_lines=[
        {"kind": "TIRE", "position": "FR", "action": "REPLACE",
         "current_tire_id": env["tires"]["FR"].id, "status": "DONE",
         "install_source": "STOCK",
         "installed_tire_id": env["stock_tire"].id}])
    _complete(mo, env)
    st = _db.session.get(Tire, env["stock_tire"].id)
    assert (st.status, st.vehicle_id, st.wheel_position) == (
        "MOUNTED", env["vehicle"].id, "FR")


def test_blank_serial_gets_a_generated_tire_id(db, env):
    mo = _mo(env, "tire_type",
             equipment_lines=[_replace_fl_from_vendor(env, new_serial="")])
    _complete(mo, env)
    new = Tire.query.filter_by(vehicle_id=env["vehicle"].id,
                               wheel_position="FL").one()
    assert new.serial_number.startswith("AUTO-")
    assert (mo.document_number or str(mo.id)) in new.serial_number


def test_not_registered_tire_gets_a_record_when_removed(db, env):
    mo = _mo(env, "tire_type", equipment_lines=[
        {"kind": "TIRE", "position": "RR", "action": "REPLACE",
         "current_tire_id": None, "current_description": "Goodyear 185/60 R15",
         "status": "DONE", "install_source": "VENDOR",
         "new_serial": "DOT-NEW-RR", "new_brand": "Bridgestone",
         "new_size": "185/60 R15"}])
    _complete(mo, env)
    old = Tire.query.filter(Tire.serial_number.like("AUTO-%-RR-OLD")).one()
    assert old.status == "IN_STOCK" and old.vehicle_id is None
    assert "Goodyear 185/60 R15" in (old.brand + " " + old.size)
    assert [t.action for t in TireTransaction.query.filter_by(
        tire_id=old.id)] == ["DISMOUNT"]


def test_rotate_and_inspect_lines_post_their_transactions(db, env):
    mo = _mo(env, "tire_type", equipment_lines=[
        {"kind": "TIRE", "position": "FL", "action": "ROTATE",
         "current_tire_id": env["tires"]["FL"].id, "rotate_to_position": "RL",
         "status": "DONE"},
        {"kind": "TIRE", "position": "FR", "action": "INSPECT",
         "current_tire_id": env["tires"]["FR"].id, "tread_depth_mm": "4.2",
         "status": "DONE"}])
    _complete(mo, env)
    fl = _db.session.get(Tire, env["tires"]["FL"].id)
    fr = _db.session.get(Tire, env["tires"]["FR"].id)
    assert fl.wheel_position == "RL"
    assert fr.current_tread_mm == Decimal("4.2") and fr.status == "MOUNTED"
    assert sorted(t.action for t in TireTransaction.query.all()) == [
        "INSPECT", "ROTATE"]


def test_battery_replace_and_test(db, env):
    mo = _mo(env, "batt_type", equipment_lines=[
        {"kind": "BATTERY", "action": "REPLACE",
         "current_battery_id": env["battery"].id, "status": "DONE",
         "install_source": "VENDOR", "new_serial": "MT-26-0001",
         "new_brand": "Motolite", "new_model": "NS40ZL",
         "new_warranty_months": 18, "voltage": "12.7",
         "test_result": "GOOD"}])
    _complete(mo, env)
    old = _db.session.get(Battery, env["battery"].id)
    assert (old.status, old.vehicle_id) == ("IN_STOCK", None)
    new = Battery.query.filter_by(serial_number="MT-26-0001").one()
    assert (new.status, new.vehicle_id, new.model, new.warranty_months) == (
        "MOUNTED", env["vehicle"].id, "NS40ZL", 18)
    assert new.last_voltage == Decimal("12.7")
    txns = BatteryTransaction.query.order_by(BatteryTransaction.id).all()
    assert [t.action for t in txns] == ["DISMOUNT", "MOUNT"]
    assert all(t.source == "MO" and t.status == "COMPLETED"
               and t.maintenance_order_id == mo.id for t in txns)


def test_completing_twice_does_not_post_twice(db, env):
    mo = _mo(env, "tire_type",
             equipment_lines=[_replace_fl_from_vendor(env)])
    _complete(mo, env)
    _complete(mo, env)
    assert TireTransaction.query.count() == 2


def test_cancelled_order_moves_nothing(db, env):
    mo = _mo(env, "tire_type",
             equipment_lines=[_replace_fl_from_vendor(env)])
    mo.status = "CANCELLED"
    db.session.commit()
    assert TireTransaction.query.count() == 0
    fl = _db.session.get(Tire, env["tires"]["FL"].id)
    assert fl.status == "MOUNTED"


def test_mo_without_lines_completes_as_before(db, env):
    mo = _mo(env, "prev_type")
    _complete(mo, env)
    assert _db.session.get(type(mo), mo.id).status == "COMPLETED"
    assert TireTransaction.query.count() == 0


def test_installed_equipment_reflects_completion(db, env):
    mo = _mo(env, "tire_type",
             equipment_lines=[_replace_fl_from_vendor(env)])
    _complete(mo, env)
    out = EquipmentWorkService().installed(env["vehicle"].id)
    assert {t["wheel_position"]: t["serial_number"]
            for t in out["tires"]}["FL"] == "DOT-4126-00412"
