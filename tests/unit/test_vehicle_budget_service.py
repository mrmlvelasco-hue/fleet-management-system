"""Tests for VehicleBudgetService — wiring up the CAR_PLAN_BUDGET_Y1..Y5
and COMPANY_OWNED_BUDGET_Y1..Y5 System Parameters that existed since
Phase 1c but were never actually consumed anywhere until this.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.core.maintenance.budget_service import VehicleBudgetService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)


@pytest.fixture()
def branch(db):
    return BranchService().create(code="BR-BUDGET", name="Budget Branch")


@pytest.fixture()
def vehicle_type(db):
    return VehicleTypeService().create(code="LV-BUDGET", name="Light",
                                       category="LIGHT")


def _vehicle(db, branch, vehicle_type, classification, delivery_date):
    v = VehicleService().create(
        vehicle_type_id=vehicle_type.id, brand="Toyota", model="Hilux",
        year=2025, branch_id=branch.id, conduction_number="BUD-000")
    v.assignment_group_classification = classification
    v.delivery_date = delivery_date
    db.session.commit()
    return v


def _seed_budget_params(db):
    """Match the real seeded defaults from cli.py exactly."""
    from app.modules.system_admin.models import SystemParameter
    values = {
        "CAR_PLAN_BUDGET_Y1": "3000", "CAR_PLAN_BUDGET_Y2": "3750",
        "CAR_PLAN_BUDGET_Y3": "4500", "CAR_PLAN_BUDGET_Y4": "5250",
        "CAR_PLAN_BUDGET_Y5": "6000",
        "COMPANY_OWNED_BUDGET_Y1": "2000", "COMPANY_OWNED_BUDGET_Y2": "2500",
        "COMPANY_OWNED_BUDGET_Y3": "3000", "COMPANY_OWNED_BUDGET_Y4": "3500",
        "COMPANY_OWNED_BUDGET_Y5": "4000",
        "BUDGET_TRACKING_MODE": "PER_YEAR",
    }
    for code, value in values.items():
        db.session.add(SystemParameter(code=code, value=value,
                                       data_type="DECIMAL" if "BUDGET_Y" in code
                                       else "STRING"))
    db.session.commit()


def test_vehicle_without_classification_is_not_applicable(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, None, date(2025, 1, 1))
    _seed_budget_params(db)
    status = VehicleBudgetService().get_budget_status(v)
    assert status["applicable"] is False


def test_vehicle_without_delivery_date_is_not_applicable(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", None)
    _seed_budget_params(db)
    status = VehicleBudgetService().get_budget_status(v)
    assert status["applicable"] is False


def test_year_1_budget_for_brand_new_vehicle(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", date(2026, 1, 1))
    _seed_budget_params(db)
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2026, 6, 1))
    assert status["current_year"] == 1
    assert status["budget"] == Decimal("3000")


def test_year_tier_advances_on_anniversary(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", date(2024, 1, 1))
    _seed_budget_params(db)
    # Just before the 2nd anniversary -> still Y2 (already past 1st anniversary)
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2025, 12, 31))
    assert status["current_year"] == 2
    assert status["budget"] == Decimal("3750")


def test_year_tier_caps_at_5(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", date(2010, 1, 1))
    _seed_budget_params(db)
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2026, 6, 1))
    assert status["current_year"] == 5
    assert status["budget"] == Decimal("6000")


def test_per_year_mode_does_not_carry_over_unspent_budget(
        db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", date(2024, 1, 1))
    _seed_budget_params(db)
    # Y1 window: 2024-01-01 to 2025-01-01, spent nothing there.
    mo = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                          scheduled_date=date(2024, 6, 1), status="COMPLETED",
                          completed_date=date(2024, 6, 1), actual_cost=100)
    db.session.add(mo)
    db.session.commit()

    # Now in Y2 (2025-01-01 to 2026-01-01) -- PER_YEAR must only count
    # spend within THIS window, not the Y1 spend above.
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2025, 6, 1))
    assert status["current_year"] == 2
    assert status["spent"] == Decimal("0")
    assert status["budget"] == Decimal("3750")


def test_accumulated_mode_sums_tiers_and_total_spend(db, branch, vehicle_type):
    from app.modules.system_admin.models import SystemParameter
    v = _vehicle(db, branch, vehicle_type, "CAR_PLAN", date(2024, 1, 1))
    _seed_budget_params(db)
    mode_param = SystemParameter.query.filter_by(
        code="BUDGET_TRACKING_MODE").first()
    mode_param.value = "ACCUMULATED"
    db.session.commit()

    mo1 = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                           scheduled_date=date(2024, 6, 1), status="COMPLETED",
                           completed_date=date(2024, 6, 1), actual_cost=1000)
    mo2 = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                           scheduled_date=date(2025, 6, 1), status="COMPLETED",
                           completed_date=date(2025, 6, 1), actual_cost=500)
    db.session.add_all([mo1, mo2])
    db.session.commit()

    # Now in Y2 -> accumulated budget = Y1(3000) + Y2(3750) = 6750;
    # accumulated spend = 1000 + 500 = 1500 (both prior MOs count).
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2025, 6, 15))
    assert status["mode"] == "ACCUMULATED"
    assert status["current_year"] == 2
    assert status["budget"] == Decimal("6750")
    assert status["spent"] == Decimal("1500")
    assert status["remaining"] == Decimal("5250")


def test_over_budget_flag(db, branch, vehicle_type):
    v = _vehicle(db, branch, vehicle_type, "COMPANY_OWNED", date(2026, 1, 1))
    _seed_budget_params(db)
    mo = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                          scheduled_date=date(2026, 3, 1), status="COMPLETED",
                          completed_date=date(2026, 3, 1), actual_cost=2500)
    db.session.add(mo)
    db.session.commit()

    # Y1 Company Owned budget is 2000, spent 2500 -> over budget.
    status = VehicleBudgetService().get_budget_status(
        v, as_of_date=date(2026, 6, 1))
    assert status["budget"] == Decimal("2000")
    assert status["spent"] == Decimal("2500")
    assert status["over_budget"] is True
