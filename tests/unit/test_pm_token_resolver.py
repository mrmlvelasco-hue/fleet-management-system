from datetime import date

import pytest

from app.core.reporting.token_resolver import resolve_pm_tokens, PM_TOKEN_LABELS
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-TOKEN", name="Token Test Branch")
    vt = VehicleTypeService().create(code="LV-TOKEN", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-TOKEN1", first_name="Juan", last_name="Dela Cruz",
        license_number="LIC-TOKEN1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        job_title="Sales Representative")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="HR-V", year=2024,
        branch_id=branch.id, conduction_number="TOKEN-000",
        plate_number="HRV001", assigned_driver_id=driver.id)
    return branch, vt, vehicle, driver


def test_resolves_vehicle_make_and_model(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Vehicle is a pm2 pm3.", vehicle=vehicle)
    assert text == "Vehicle is a Honda HR-V."


def test_resolves_plate_number(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Plate: pm4", vehicle=vehicle)
    assert text == "Plate: HRV001"


def test_resolves_assignee_and_position(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Assigned to pm5, pm7.", vehicle=vehicle)
    assert text == "Assigned to Juan Dela Cruz, Sales Representative."


def test_resolves_branch(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Office: pm6", vehicle=vehicle)
    assert text == "Office: Token Test Branch"


def test_resolves_last_work_order_number_and_date(db, env):
    branch, vt, vehicle, driver = env
    mt = MaintenanceTypeService().create(code="TOKEN-MT", name="Token Test MT",
                                         category="PM")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.status = "COMPLETED"
    order.completed_date = date(2026, 6, 15)
    order.document_number = "MO-2026-000099"
    db.session.commit()

    text = resolve_pm_tokens(
        "Last date replaced : pm9 Reference W.O.# pm8", vehicle=vehicle)
    assert "MO-2026-000099" in text
    assert "2026-06-15" in text


def test_missing_data_resolves_to_empty_not_crash(db, env):
    branch, vt, vehicle, driver = env
    vehicle.assigned_driver_id = None
    db.session.commit()
    text = resolve_pm_tokens("Assignee: pm5, Position: pm7", vehicle=vehicle)
    assert text == "Assignee: , Position: "


def test_no_vehicle_at_all_does_not_crash(db):
    text = resolve_pm_tokens("Vehicle: pm2 pm3, Plate: pm4", vehicle=None)
    assert text == "Vehicle:  , Plate: "


def test_unrecognized_tokens_left_as_is(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Unknown token pm99 stays.", vehicle=vehicle)
    assert "pm99" in text


def test_none_text_returns_none(db):
    assert resolve_pm_tokens(None, vehicle=None) is None


def test_empty_text_returns_empty(db):
    assert resolve_pm_tokens("", vehicle=None) == ""


def test_case_insensitive_token_matching(db, env):
    branch, vt, vehicle, driver = env
    text = resolve_pm_tokens("Plate: PM4 / Pm4", vehicle=vehicle)
    assert text == "Plate: HRV001 / HRV001"


def test_token_labels_documented_for_admin_reference(db):
    """The legend (pm2=Vehicle Make, pm3=Vehicle Model, etc.) should be
    available programmatically too, not just hardcoded in this module --
    useful for an admin help screen listing available tokens."""
    assert PM_TOKEN_LABELS["pm2"] == "Vehicle Make"
    assert PM_TOKEN_LABELS["pm8"] == "Last Work Order"
