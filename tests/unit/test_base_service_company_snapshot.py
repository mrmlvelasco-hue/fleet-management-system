"""Stage 3B (D4/D7): BaseTransactionService must snapshot a submitted
record's Company onto its ApprovalInstance, the same way it already does
for branch_id via _infer_branch_id -- so ORG_COMPANY/ORG_REGION/
ORG_AREA_DISTRICT approval eligibility (tests/unit/test_approval_org_scope.py)
has something real to check against for an actual transaction module, not
just a hand-built engine.submit() call.

Trip Ticket is used as the exercising module purely because it is the
simplest existing BaseTransactionService subclass with a vehicle_id FK --
this is a generic base_service behavior, not a trip-ticket-specific one.
"""
from datetime import datetime, date

import pytest

from tests.conftest import tiny_jpeg_file
from app.extensions import db as _db
from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.system_admin.models import SystemParameter
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-COSNAP", name="Company Snapshot Branch")
    region = RegionService().create(code="RG-COSNAP", name="Company Snapshot Region")
    area = AreaDistrictService().create(code="AD-COSNAP", name="Company Snapshot Area",
                                        region_id=region.id)
    company = OrgCompanyService().create(short_name="Snapshot Co",
                                         region_id=region.id,
                                         area_district_id=area.id)
    vt = VehicleTypeService().create(code="LV-COSNAP", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch.id, conduction_number="COSNAP-000",
        company_id=company.id)
    driver = DriverService().create(
        employee_number="EMP-COSNAP", first_name="Juan", last_name="Cruz",
        license_number="LIC-COSNAP", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id,
        photo_file=tiny_jpeg_file())
    user = User(username="cosnap_requester", email="cosnap@x.com",
               password_hash="x")
    _db.session.add(user)
    _db.session.add(SystemParameter(code="REQUIRE_DRIVER_FROM_MASTER",
                                    value="NO", data_type="STRING",
                                    group_name="TRIP_TICKET"))
    _db.session.commit()

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return vehicle, driver, user, company


def test_submit_snapshots_the_vehicles_company_onto_the_approval_instance(
        db, env):
    vehicle, driver, user, company = env
    svc = TripTicketService()
    tt = svc.create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        destination="Batangas", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 15, 8, 0),
        odometer_out=1000, user=user)
    tt = svc.submit(tt.id, user)
    assert tt.approval_instance.company_id == company.id
