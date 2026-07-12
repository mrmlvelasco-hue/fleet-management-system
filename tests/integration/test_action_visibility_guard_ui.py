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
    role = Role(name="ActionGuardUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="actionguard_ui", email="actionguard_ui@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "actionguard_ui", "password": "pw123456"})
    return u


def test_cancel_action_on_out_of_scope_record_does_not_crash(client, db):
    """This is the exact gap: previously, POSTing directly to the cancel
    action URL for a record outside your scope would either succeed
    silently (no protection at all) or crash with an unhandled 500 if the
    guard were added without also fixing the route's except clause."""
    branch_mine = BranchService().create(code="BR-ACTUI1", name="My Branch Act")
    branch_other = BranchService().create(code="BR-ACTUI2", name="Other Branch Act")
    user = _login(client, db, codes=["tripticket.view", "tripticket.update"])
    UserOrgScopeService().assign(user.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    vt = VehicleTypeService().create(code="LV-ACTUI", name="Light", category="LIGHT")
    other_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Innova", year=2024,
        branch_id=branch_other.id, conduction_number="ACTUI-000")
    driver = DriverService().create(
        employee_number="EMP-ACTUI1", first_name="Zeny", last_name="Ocampo",
        license_number="LIC-ACTUI1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_other.id)
    other_requester = User(username="actui_other", email="actui_other@x.com",
                           password_hash="x")
    db.session.add(other_requester)
    db.session.commit()

    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="TT").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    trip = TripTicketService().create(
        vehicle_id=other_vehicle.id, driver_id=driver.id, destination="Zamboanga",
        purpose="Cargo", departure_datetime=datetime(2026, 7, 24, 8, 0),
        odometer_out=300, user=other_requester)

    # Directly POST to the cancel action for a record outside this user's
    # scope — must not crash (500), and must not actually cancel it.
    resp = client.post(f"/transactions/trip-tickets/{trip.id}/cancel",
                       follow_redirects=False)
    assert resp.status_code in (302, 403)

    from app.modules.transactions.trip_ticket.models import TripTicket
    assert db.session.get(TripTicket, trip.id).status != "CANCELLED"
