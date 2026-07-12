from datetime import date, datetime

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.user_management.org_scope_service import UserOrgScopeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.trip_ticket.service import TripTicketService


def _login(client, db, *, codes=()):
    role = Role(name="ViewScopeUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="scoped_ui_user", email="scoped_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "scoped_ui_user", "password": "pw123456"})
    return u


def test_direct_url_access_to_out_of_scope_record_returns_403(client, db):
    """This is the key check: not just list-hiding, but genuine access
    control — a scoped user cannot view another branch's record even by
    guessing/typing the URL directly."""
    user = _login(client, db, codes=["tripticket.view"])
    branch_mine = BranchService().create(code="BR-UISCOPE1", name="My Branch UI")
    branch_other = BranchService().create(code="BR-UISCOPE2", name="Other Branch UI")
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    vt = VehicleTypeService().create(code="LV-UISCOPE", name="Light", category="LIGHT")
    other_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hiace", year=2024,
        branch_id=branch_other.id, conduction_number="UISCOPE-000")
    driver = DriverService().create(
        employee_number="EMP-UISCOPE1", first_name="Rico", last_name="Bautista",
        license_number="LIC-UISCOPE1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_other.id)

    other_requester = User(username="other_branch_requester",
                           email="other_branch_requester@x.com",
                           password_hash="x")
    db.session.add(other_requester)
    db.session.commit()

    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)
    NumberingSchemeService().create(
        document_type_id=1, prefix="TT", include_year=True,
        digit_count=6, reset_policy="YEARLY")

    trip = TripTicketService().create(
        vehicle_id=other_vehicle.id, driver_id=driver.id, destination="Iloilo",
        purpose="Cargo", departure_datetime=datetime(2026, 7, 22, 8, 0),
        odometer_out=500, user=other_requester)

    resp = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert resp.status_code == 403

    # Confirm it's genuinely absent from the list too
    list_resp = client.get("/transactions/trip-tickets")
    assert trip.document_number.encode() not in list_resp.data


def test_direct_url_access_to_own_submission_always_works(client, db):
    user = _login(client, db, codes=["tripticket.view", "tripticket.create"])
    branch_mine = BranchService().create(code="BR-UISCOPE3", name="My Branch UI 2")
    branch_other = BranchService().create(code="BR-UISCOPE4", name="Other Branch UI 2")
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    vt = VehicleTypeService().create(code="LV-UISCOPE2", name="Light", category="LIGHT")
    other_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch_other.id, conduction_number="UISCOPE-001")
    driver = DriverService().create(
        employee_number="EMP-UISCOPE2", first_name="Wendy", last_name="Torres",
        license_number="LIC-UISCOPE2", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_other.id)

    DocumentTypeService().create(code="TT2", name="Trip Ticket 2",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="TT2").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT2",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    class _FakeService(TripTicketService):
        document_type_code = "TT2"

    trip = _FakeService().create(
        vehicle_id=other_vehicle.id, driver_id=driver.id, destination="Cebu",
        purpose="Own submission test", departure_datetime=datetime(2026, 7, 23, 8, 0),
        odometer_out=700, user=user)

    resp = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert resp.status_code == 200
