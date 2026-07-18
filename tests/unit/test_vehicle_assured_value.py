from datetime import date
from decimal import Decimal

import pytest

from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ASSURED", name="Assured Value Branch")
    vt = VehicleTypeService().create(code="LV-ASSURED", name="Light", category="LIGHT")
    return branch, vt


def test_assured_value_same_year_as_delivery_equals_acquisition_cost(db, env):
    """Zero full years elapsed -> no depreciation applied yet."""
    branch, vt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSURED-000",
        acquisition_cost=1000000, delivery_date=date(2026, 1, 15))
    value = vehicle.compute_assured_value(as_of_date=date(2026, 6, 1))
    assert value == Decimal("1000000.00")


def test_assured_value_after_one_full_year_depreciates_10_percent(db, env):
    branch, vt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSURED-001",
        acquisition_cost=1000000, delivery_date=date(2025, 1, 15))
    value = vehicle.compute_assured_value(as_of_date=date(2026, 6, 1))
    assert value == Decimal("900000.00")


def test_assured_value_compounds_after_multiple_years(db, env):
    """3 full years: 1,000,000 * 0.9^3 = 729,000."""
    branch, vt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSURED-002",
        acquisition_cost=1000000, delivery_date=date(2023, 1, 15))
    value = vehicle.compute_assured_value(as_of_date=date(2026, 6, 1))
    assert value == Decimal("729000.00")


def test_assured_value_none_without_acquisition_cost(db, env):
    branch, vt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSURED-003",
        delivery_date=date(2023, 1, 15))
    assert vehicle.compute_assured_value() is None


def test_assured_value_none_without_delivery_date(db, env):
    branch, vt = env
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="ASSURED-004",
        acquisition_cost=1000000)
    assert vehicle.compute_assured_value() is None
