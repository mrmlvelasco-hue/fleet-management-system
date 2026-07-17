from datetime import date, timedelta

import pytest

from app.modules.registration_config.service import (
    RegistrationTemplateService, RegistrationDueCalculationService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-REGPMS", name="Reg PMS Branch")
    vt = VehicleTypeService().create(code="LV-REGPMS", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="REGPMS-000")
    return branch, vt, vehicle


def test_create_registration_template(db, env):
    branch, vt, vehicle = env
    tmpl = RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3,
        notify_before_days=30, priority="MEDIUM")
    assert tmpl.interval_years == 3
    assert tmpl.next_generation_policy == "AUTO_SCHEDULE"  # default


def test_create_template_with_checklist_items(db, env):
    branch, vt, vehicle = env
    tmpl = RegistrationTemplateService().create(
        vehicle_type_id=vt.id, interval_years=3,
        items=[
            {"activity_code": "OR-CR", "activity_description": "Renew OR/CR", "sort_order": 1},
            {"activity_code": "EMISSION", "activity_description": "Emission Test", "sort_order": 2},
            {"activity_code": "INSURANCE", "activity_description": "Insurance Renewal Check", "sort_order": 3},
        ])
    assert len(tmpl.checklist_items) == 3
    assert tmpl.checklist_items[0].activity_code == "OR-CR"


def test_due_status_good_when_far_from_expiry(db, env):
    branch, vt, vehicle = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=30), user=None)
    reg.status = "COMPLETED"
    from app.extensions import db as _db
    _db.session.commit()

    status = RegistrationDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "GOOD"


def test_due_status_overdue_when_expired(db, env):
    branch, vt, vehicle = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 + 10), user=None)
    reg.status = "COMPLETED"
    from app.extensions import db as _db
    _db.session.commit()

    status = RegistrationDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "OVERDUE"


def test_due_status_due_soon_within_notify_window(db, env):
    branch, vt, vehicle = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3,
                                         notify_before_days=30)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today() - timedelta(days=3 * 365 - 10), user=None)
    reg.status = "COMPLETED"
    from app.extensions import db as _db
    _db.session.commit()

    status = RegistrationDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "DUE_SOON"


def test_never_registered_vehicle_has_no_due_status(db, env):
    """A vehicle with no completed registration on record at all has
    nothing to calculate from -- distinct from GOOD, since there's no
    baseline expiry date yet (e.g. brand new, first NEW registration
    still pending)."""
    branch, vt, vehicle = env
    RegistrationTemplateService().create(vehicle_type_id=vt.id, interval_years=3)
    status = RegistrationDueCalculationService().get_due_status(vehicle)
    assert status["status"] == "NO_RECORD"
