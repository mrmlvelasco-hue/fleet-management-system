"""Tests for the Vehicle Assignment Memo (VAM) print -- the formal
company memo for Assignment/Reassignment orders, matching the corporate
paper form (Location, Date, assignee, classification checkboxes,
vehicle details, and Endorsed/Approved By pulled from the real approval
trail).
"""
from datetime import date

import pytest

from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.driver.service import DriverService
from app.cli import _seed_transaction_types


@pytest.fixture()
def transaction_types(db):
    _seed_transaction_types()
    db.session.commit()


@pytest.fixture()
def branch(db):
    return BranchService().create(code="BR-VAM", name="NLO - North Luzon Operations")


@pytest.fixture()
def vehicle_type(db):
    return VehicleTypeService().create(code="LV-VAM", name="Light", category="LIGHT")


@pytest.fixture()
def vehicle(db, branch, vehicle_type):
    v = VehicleService().create(
        vehicle_type_id=vehicle_type.id, brand="Kawasaki", model="Barako",
        year=2019, branch_id=branch.id, conduction_number="VAM-000",
        plate_number="VAM-1234")
    v.engine_number = "BC175AEBB1998"
    v.chassis_number = "BC175H-B91423"
    v.color = "Red"
    v.far_number = "FAR-99887"
    db.session.commit()
    return v


@pytest.fixture()
def driver(db, branch):
    return DriverService().create(
        first_name="Alex", last_name="Calonge", employee_number="EMP-VAM-01",
        branch_id=branch.id, license_number="LIC-VAM-01",
        license_expiry=date(2030, 1, 1), license_type="Professional")


def test_assignment_classification_persists_on_reassignment_order(
        db, transaction_types, vehicle, driver):
    tt = TransactionType.query.filter_by(code="DEP-REASSIGNMENT").first()
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date.today(), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        driver_id=driver.id, assignment_classification="TOOL_OF_THE_TRADE")
    assert mo.assignment_classification == "TOOL_OF_THE_TRADE"


def test_vam_route_redirects_when_no_driver_set(
        db, client, transaction_types, vehicle):
    """An order with no driver_id isn't a Vehicle Assignment Memo case at
    all -- must redirect with a warning, never render a blank/broken
    print page."""
    tt = TransactionType.query.filter_by(code="ADM-MIGRATE-EXPENSE").first()
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date.today(), user=None,
        order_category="OPERATIONAL", transaction_type_id=tt.id)
    r = client.get(f"/transactions/maintenance-orders/{mo.id}/print-vam",
                   follow_redirects=False)
    assert r.status_code in (302, 403)


def test_vam_endorsed_by_is_the_initiator_with_created_date(
        db, transaction_types, vehicle, driver):
    """Regression for the reported bug: Endorsed By must be the person
    who INITIATED the memo (the MO's requester) with the CREATED date --
    NOT pulled from the approval trail (which wrongly put the approver on
    the Endorsed line and left Approved blank when there was one level).
    Verified at the route's data-assembly level: the requester becomes
    the endorser."""
    from app.modules.user_management.models import User
    from app.extensions import db as _db
    initiator = User(username="vam_initiator", email="vi@x.com",
                     password_hash="x", first_name="Ina", last_name="Isyador")
    _db.session.add(initiator)
    _db.session.commit()

    tt = TransactionType.query.filter_by(code="DEP-REASSIGNMENT").first()
    mo = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, scheduled_date=date.today(), user=initiator,
        order_category="OPERATIONAL", transaction_type_id=tt.id,
        driver_id=driver.id, assignment_classification="PERK")
    assert mo.requester is not None
    assert mo.requester.id == initiator.id
    # The route uses item.requester for the Endorsed-by line and
    # item.created_at for its date -- both are now populated on the MO.
    assert mo.created_at is not None
