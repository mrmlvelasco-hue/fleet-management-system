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
    branch_a = BranchService().create(code="BR-VIEWSCOPE-A", name="Manila View")
    branch_b = BranchService().create(code="BR-VIEWSCOPE-B", name="Cebu View")
    vt = VehicleTypeService().create(code="LV-VIEWSCOPE", name="Light", category="LIGHT")

    manila_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch_a.id, conduction_number="VIEWSCOPE-A")
    cebu_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch_b.id, conduction_number="VIEWSCOPE-B")
    driver = DriverService().create(
        employee_number="EMP-VIEWSCOPE1", first_name="Lea", last_name="Santos",
        license_number="LIC-VIEWSCOPE1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_a.id)

    requester = User(username="viewscope_requester",
                     email="viewscope_requester@x.com", password_hash="x")
    manila_viewer = User(username="viewscope_manila",
                        email="viewscope_manila@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([requester, manila_viewer])
    _db.session.commit()
    UserOrgScopeService().assign(manila_viewer.id, scope_type="BRANCH",
                                branch_id=branch_a.id)

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    manila_trip = TripTicketService().create(
        vehicle_id=manila_vehicle.id, driver_id=driver.id,
        destination="Tagaytay", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 20, 8, 0), odometer_out=1000,
        user=requester)
    cebu_trip = TripTicketService().create(
        vehicle_id=cebu_vehicle.id, driver_id=driver.id,
        destination="Davao", purpose="Delivery",
        departure_datetime=datetime(2026, 7, 21, 8, 0), odometer_out=2000,
        user=requester)

    return branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester


def test_scoped_user_only_sees_records_in_their_branch(db, env):
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    visible = TripTicketService().list(user=manila_viewer)
    visible_ids = {t.id for t in visible}
    assert manila_trip.id in visible_ids
    assert cebu_trip.id not in visible_ids


def test_user_with_no_scope_sees_everything(db, env):
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    unscoped_user = User(username="viewscope_unscoped",
                         email="viewscope_unscoped@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped_user)
    _db.session.commit()
    visible = TripTicketService().list(user=unscoped_user)
    visible_ids = {t.id for t in visible}
    assert manila_trip.id in visible_ids
    assert cebu_trip.id in visible_ids


def test_requester_always_sees_their_own_submission_regardless_of_scope(db, env):
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    # requester has no scope at all, so this also tests the "no scope"
    # fallback — but specifically confirms self-visibility holds even
    # if we later tighten "no scope" to be restrictive by default.
    visible = TripTicketService().list(user=requester)
    visible_ids = {t.id for t in visible}
    assert manila_trip.id in visible_ids
    assert cebu_trip.id in visible_ids


def test_list_without_user_arg_shows_everything_unchanged(db, env):
    """Backward compatibility: existing callers that don't pass `user`
    keep getting the full unfiltered list, exactly as before."""
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    visible = TripTicketService().list()
    visible_ids = {t.id for t in visible}
    assert manila_trip.id in visible_ids
    assert cebu_trip.id in visible_ids


def test_get_visible_returns_none_for_out_of_scope_record(db, env):
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    assert TripTicketService().get_visible(cebu_trip.id, manila_viewer) is None
    assert TripTicketService().get_visible(manila_trip.id, manila_viewer) is not None


def test_get_visible_returns_record_for_own_submission(db, env):
    branch_a, branch_b, manila_trip, cebu_trip, manila_viewer, requester = env
    assert TripTicketService().get_visible(cebu_trip.id, requester) is not None
