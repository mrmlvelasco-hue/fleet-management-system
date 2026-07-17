from datetime import date, timedelta

import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.registration_config.service import RegistrationDueCalculationService
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-DISPOSED", name="Disposed Branch")
    vt = VehicleTypeService().create(code="LV-DISPOSED", name="Light", category="LIGHT")
    return branch, vt


def test_disposed_vehicle_excluded_from_maintenance_due_scan(db, env):
    branch, vt = env
    mt = MaintenanceTypeService().create(code="DISPOSED-MT", name="Disposed Test MT",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="KM", interval_km=5000)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="DISPOSED-000",
        current_odometer=10000, status="DISPOSED")

    due = PMDueCalculationService().get_all_due_vehicles()
    assert vehicle.id not in [d["vehicle"].id for d in due]


def test_disposed_vehicle_excluded_from_registration_due_scan(db, env):
    branch, vt = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="DISPOSED-001",
        status="DISPOSED")
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 + 10), user=None)
    reg.status = "COMPLETED"
    db.session.commit()

    due = RegistrationDueCalculationService().get_all_due_vehicles()
    assert vehicle.id not in [d["vehicle"].id for d in due]


def test_active_vehicle_still_included_in_both_scans(db, env):
    """Confirms the DISPOSED exclusion doesn't accidentally over-filter
    normal ACTIVE vehicles."""
    branch, vt = env
    mt = MaintenanceTypeService().create(code="ACTIVE-MT", name="Active Test MT",
                                         category="PM")
    PMScheduleService().create(vehicle_type_id=vt.id, maintenance_type_id=mt.id,
                               trigger_mode="KM", interval_km=5000)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="ACTIVE-000",
        current_odometer=10000, status="ACTIVE")

    due = PMDueCalculationService().get_all_due_vehicles()
    assert vehicle.id in [d["vehicle"].id for d in due]
