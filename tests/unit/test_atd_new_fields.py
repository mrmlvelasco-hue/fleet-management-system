from datetime import date

import pytest

from app.modules.transactions.atd.service import ATDService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ATDFIELDS", name="ATD Fields Branch")
    vt = VehicleTypeService().create(code="LV-ATDFIELDS", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="NKR 60", year=2024,
        branch_id=branch.id, conduction_number="ATDFIELDS-000",
        plate_number="WJR-408")
    driver = DriverService().create(
        employee_number="EMP-ATDFIELDS1", first_name="Alwin", last_name="Delo Santos",
        license_number="LIC-ATDFIELDS1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    return branch, vt, vehicle, driver


def test_atd_can_have_odometer_out(db, env):
    branch, vt, vehicle, driver = env
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Assigned unit is due for preventive maintenance.",
        valid_from=date.today(), valid_to=date.today(),
        odometer_out=15000, user=None)
    assert atd.odometer_out == 15000


def test_atd_can_link_to_a_maintenance_order(db, env):
    branch, vt, vehicle, driver = env
    mt = MaintenanceTypeService().create(code="ATDFIELDS-MT", name="ATD Field Test MT",
                                         category="PM")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)

    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Assigned unit is due for preventive maintenance.",
        valid_from=date.today(), valid_to=date.today(),
        maintenance_order_id=order.id, user=None)
    assert atd.maintenance_order_id == order.id
    assert atd.maintenance_order.document_number is not None


def test_atd_fields_are_optional(db, env):
    branch, vt, vehicle, driver = env
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="General errand", valid_from=date.today(), valid_to=date.today(),
        user=None)
    assert atd.odometer_out is None
    assert atd.maintenance_order_id is None


def test_record_odometer_in_on_return(db, env):
    branch, vt, vehicle, driver = env
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="General errand", valid_from=date.today(), valid_to=date.today(),
        odometer_out=15000, user=None)
    ATDService().record_odometer_in(atd.id, odometer_in=15120)

    from app.modules.transactions.atd.models import AuthorityToDrive
    updated = db.session.get(AuthorityToDrive, atd.id)
    assert updated.odometer_in == 15120
