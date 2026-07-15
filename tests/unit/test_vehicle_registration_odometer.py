from datetime import date

import pytest

from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VROD", name="VR Odometer Branch")
    vt = VehicleTypeService().create(code="LV-VROD", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="VROD-000")
    DocumentTypeService().create(code="VR", name="Vehicle Registration",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="VR").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, vehicle


def test_create_registration_with_current_odometer(db, env):
    branch, vt, vehicle = env
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today(), odometer_at_registration=3500,
        user=None)
    assert reg.odometer_at_registration == 3500


def test_odometer_is_optional(db, env):
    branch, vt, vehicle = env
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date.today(), user=None)
    assert reg.odometer_at_registration is None
