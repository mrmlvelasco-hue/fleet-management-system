from datetime import date

import pytest

from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService)
from app.modules.transactions.battery_txn.models import BatteryTransaction
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-TX", name="TX Branch")
    vt = VehicleTypeService().create(code="LV-TX", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Innova", year=2024,
        branch_id=branch.id, conduction_number="TX-000")
    vendor = VendorService().create(code="VEN-TX", name="Tire Shop")
    tire = TireService().create(serial_number="TIRE-TX1", brand="Bridgestone",
                                size="265/65R17", tire_type="RADIAL",
                                vendor_id=vendor.id)
    battery = BatteryService().create(serial_number="BAT-TX1", brand="Motolite",
                                      vendor_id=vendor.id)
    user = User(username="tx_requester", email="tx@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()

    for code, name in [("TIR", "Tire Transaction"), ("BAT", "Battery Transaction")]:
        dt = DocumentTypeService().create(code=code, name=name,
                                          requires_approval=False,
                                          auto_numbering=True)
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    return vehicle, tire, battery, user


def test_mount_tire_sets_status_and_vehicle_link(db, env):
    vehicle, tire, battery, user = env
    svc = TireTransactionService()
    txn = svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
                     transaction_date=date(2026, 7, 15),
                     odometer_at_service=5000, user=user)
    assert txn.document_number.startswith("TIR-")
    refreshed_tire = TireService().get(tire.id)
    assert refreshed_tire.status == "MOUNTED"


def test_dismount_tire_frees_to_stock(db, env):
    vehicle, tire, battery, user = env
    svc = TireTransactionService()
    svc.create(tire_id=tire.id, vehicle_id=vehicle.id, action="MOUNT",
              transaction_date=date(2026, 7, 15), user=user)
    svc.create(tire_id=tire.id, vehicle_id=None, action="DISMOUNT",
              transaction_date=date(2026, 8, 1), user=user)
    refreshed_tire = TireService().get(tire.id)
    assert refreshed_tire.status == "IN_STOCK"


def test_dispose_tire_deactivates_record(db, env):
    vehicle, tire, battery, user = env
    svc = TireTransactionService()
    svc.create(tire_id=tire.id, vehicle_id=None, action="DISPOSE",
              transaction_date=date(2026, 8, 1), user=user)
    refreshed_tire = TireService().get(tire.id)
    assert refreshed_tire.status == "DISPOSED"


def test_mount_battery_sets_status_and_vehicle_link(db, env):
    vehicle, tire, battery, user = env
    svc = BatteryTransactionService()
    txn = svc.create(battery_id=battery.id, vehicle_id=vehicle.id,
                     action="MOUNT", transaction_date=date(2026, 7, 15),
                     user=user)
    assert txn.document_number.startswith("BAT-")
    refreshed_battery = BatteryService().get(battery.id)
    assert refreshed_battery.status == "MOUNTED"


def test_dismount_battery_frees_to_stock(db, env):
    vehicle, tire, battery, user = env
    svc = BatteryTransactionService()
    svc.create(battery_id=battery.id, vehicle_id=vehicle.id, action="MOUNT",
              transaction_date=date(2026, 7, 15), user=user)
    svc.create(battery_id=battery.id, vehicle_id=None, action="DISMOUNT",
              transaction_date=date(2026, 8, 1), user=user)
    refreshed_battery = BatteryService().get(battery.id)
    assert refreshed_battery.status == "IN_STOCK"
