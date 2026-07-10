import pytest
from datetime import date, timedelta

from app.modules.master_data.vehicle.service import (
    VehicleService, DuplicateVehicleError)
from app.modules.master_data.driver.service import (
    DriverService, DuplicateDriverError)
from app.modules.master_data.tire.service import (
    TireService, DuplicateSerialError)
from app.modules.master_data.battery.service import (
    BatteryService, DuplicateSerialError as DupBatteryError)
from app.modules.master_data.vendor.service import (
    VendorService, DuplicateCodeError as DupVendorCode)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService


@pytest.fixture()
def branch(db):
    return BranchService().create(code="HQ", name="Head Office")


@pytest.fixture()
def vtype(db):
    return VehicleTypeService().create(
        code="LV", name="Light Vehicle", category="LIGHT")


# ---------- Vehicle ----------

def test_create_vehicle(db, branch, vtype):
    svc = VehicleService()
    v = svc.create(
        conduction_number="CN-2026-001",
        vehicle_type_id=vtype.id, brand="Toyota", model="Hilux",
        year=2026, color="White", fuel_type="DIESEL",
        branch_id=branch.id, acquisition_date=date.today(),
        acquisition_cost=1_500_000)
    assert v.id is not None
    assert v.plate_number is None  # conduction phase


def test_assign_plate_number(db, branch, vtype):
    svc = VehicleService()
    v = svc.create(
        conduction_number="CN-2026-002", vehicle_type_id=vtype.id,
        brand="Isuzu", model="D-MAX", year=2025, branch_id=branch.id)
    svc.assign_plate(v.id, "ABC-1234")
    assert svc.get(v.id).plate_number == "ABC-1234"


def test_duplicate_conduction_rejected(db, branch, vtype):
    svc = VehicleService()
    svc.create(conduction_number="CN-DUP", vehicle_type_id=vtype.id,
               brand="X", model="Y", year=2024, branch_id=branch.id)
    with pytest.raises(DuplicateVehicleError):
        svc.create(conduction_number="CN-DUP", vehicle_type_id=vtype.id,
                   brand="A", model="B", year=2024, branch_id=branch.id)


def test_update_odometer(db, branch, vtype):
    svc = VehicleService()
    v = svc.create(conduction_number="CN-ODO", vehicle_type_id=vtype.id,
                   brand="M", model="N", year=2023, branch_id=branch.id,
                   current_odometer=10000)
    svc.update_odometer(v.id, 15000)
    assert svc.get(v.id).current_odometer == 15000


def test_deactivate_vehicle(db, branch, vtype):
    svc = VehicleService()
    v = svc.create(conduction_number="CN-DEL", vehicle_type_id=vtype.id,
                   brand="D", model="E", year=2022, branch_id=branch.id)
    svc.deactivate(v.id)
    assert not svc.get(v.id).is_active


# ---------- Driver ----------

def test_create_driver(db, branch):
    svc = DriverService()
    d = svc.create(
        employee_number="EMP-001", first_name="Juan",
        last_name="dela Cruz", license_number="N01-12-345678",
        license_expiry=date.today() + timedelta(days=365),
        license_type="PRO", branch_id=branch.id)
    assert d.id is not None


def test_duplicate_license_rejected(db, branch):
    svc = DriverService()
    svc.create(employee_number="EMP-002", first_name="Ana",
               last_name="Santos", license_number="N01-99-999999",
               license_expiry=date.today() + timedelta(days=365),
               license_type="NON-PRO", branch_id=branch.id)
    with pytest.raises(DuplicateDriverError):
        svc.create(employee_number="EMP-003", first_name="Bob",
                   last_name="Reyes", license_number="N01-99-999999",
                   license_expiry=date.today() + timedelta(days=365),
                   license_type="NON-PRO", branch_id=branch.id)


def test_expiring_licenses(db, branch):
    svc = DriverService()
    svc.create(employee_number="EMP-004", first_name="Carlos",
               last_name="Lopez", license_number="N01-11-111111",
               license_expiry=date.today() + timedelta(days=20),
               license_type="PRO", branch_id=branch.id)
    svc.create(employee_number="EMP-005", first_name="Diana",
               last_name="Cruz", license_number="N01-22-222222",
               license_expiry=date.today() + timedelta(days=200),
               license_type="PRO", branch_id=branch.id)
    expiring = svc.get_expiring_licenses(days=30)
    assert len(expiring) == 1
    assert expiring[0].employee_number == "EMP-004"


# ---------- Tire ----------

def test_create_tire(db):
    v = VendorService().create(
        code="VND1", name="Bridgestone PH",
        vendor_type="GOODS")
    t = TireService().create(
        serial_number="TIR-001", brand="Bridgestone",
        size="265/65R17", tire_type="RADIAL",
        purchase_date=date.today(), purchase_cost=8000,
        vendor_id=v.id)
    assert t.status == "IN_STOCK"


def test_duplicate_tire_serial_rejected(db):
    VendorService().create(code="VND2", name="Vendor 2", vendor_type="GOODS")
    v = VendorService().list()[0]
    TireService().create(serial_number="TIR-DUP", brand="X", size="Y",
                         tire_type="RADIAL", purchase_date=date.today(),
                         purchase_cost=1, vendor_id=v.id)
    with pytest.raises(DuplicateSerialError):
        TireService().create(serial_number="TIR-DUP", brand="X", size="Y",
                             tire_type="RADIAL", purchase_date=date.today(),
                             purchase_cost=1, vendor_id=v.id)


# ---------- Battery ----------

def test_create_battery(db):
    v = VendorService().create(
        code="VND3", name="Motolite", vendor_type="GOODS")
    b = BatteryService().create(
        serial_number="BAT-001", brand="Motolite",
        capacity_ah=60, voltage=12,
        purchase_date=date.today(), purchase_cost=5000,
        vendor_id=v.id)
    assert b.status == "IN_STOCK"


# ---------- Vendor ----------

def test_create_vendor(db):
    v = VendorService().create(
        code="VND-TEST", name="Test Vendor",
        vendor_type="BOTH", city="Quezon City")
    assert v.code == "VND-TEST"


def test_duplicate_vendor_code_rejected(db):
    VendorService().create(code="DUP", name="Dup", vendor_type="GOODS")
    with pytest.raises(DupVendorCode):
        VendorService().create(code="DUP", name="Dup2", vendor_type="GOODS")
