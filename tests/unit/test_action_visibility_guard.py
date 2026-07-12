from datetime import date, datetime

import pytest

from app.modules.transactions.trip_ticket.service import TripTicketService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    branch_a = BranchService().create(code="BR-ACTGUARD-A", name="Manila Action")
    branch_b = BranchService().create(code="BR-ACTGUARD-B", name="Cebu Action")
    vt = VehicleTypeService().create(code="LV-ACTGUARD", name="Light", category="LIGHT")

    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch_a.id, conduction_number="ACTGUARD-000")
    driver = DriverService().create(
        employee_number="EMP-ACTGUARD1", first_name="Lino", last_name="Ramos",
        license_number="LIC-ACTGUARD1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_a.id)

    requester = User(username="actguard_requester",
                     email="actguard_requester@x.com", password_hash="x")
    outsider = User(username="actguard_outsider",
                    email="actguard_outsider@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([requester, outsider])
    _db.session.commit()
    UserOrgScopeService().assign(outsider.id, scope_type="BRANCH",
                                branch_id=branch_b.id)

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Baguio",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=requester)

    return branch_a, branch_b, trip, requester, outsider


def test_outsider_cannot_submit_someone_elses_draft(db, env):
    from app.modules.transactions.base_service import NotVisibleError
    branch_a, branch_b, trip, requester, outsider = env
    with pytest.raises(NotVisibleError):
        TripTicketService().submit(trip.id, user=outsider)


def test_requester_can_submit_their_own_draft(db, env):
    branch_a, branch_b, trip, requester, outsider = env
    submitted = TripTicketService().submit(trip.id, user=requester)
    assert submitted.approval_instance_id is not None


def test_outsider_cannot_cancel_someone_elses_transaction(db, env):
    from app.modules.transactions.base_service import NotVisibleError
    branch_a, branch_b, trip, requester, outsider = env
    with pytest.raises(NotVisibleError):
        TripTicketService().cancel(trip.id, user=outsider)


def test_requester_can_cancel_their_own_transaction(db, env):
    branch_a, branch_b, trip, requester, outsider = env
    cancelled = TripTicketService().cancel(trip.id, user=requester)
    assert cancelled.status == "CANCELLED"


def test_scoped_user_covering_the_branch_can_still_submit(db, env):
    """Someone whose scope legitimately covers the record's branch (not
    just the requester themselves) should still be able to act on it."""
    branch_a, branch_b, trip, requester, outsider = env
    covering_user = User(username="actguard_covering",
                         email="actguard_covering@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add(covering_user)
    _db.session.commit()
    UserOrgScopeService().assign(covering_user.id, scope_type="BRANCH",
                                branch_id=branch_a.id)
    submitted = TripTicketService().submit(trip.id, user=covering_user)
    assert submitted.approval_instance_id is not None
