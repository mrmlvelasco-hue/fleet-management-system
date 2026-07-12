from datetime import date, datetime

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.trip_ticket.service import TripTicketService


def _login(client, db, *, username, codes=()):
    role = Role(name=f"DashRole-{username}")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username=username, email=f"{username}@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": username, "password": "pw123456"})
    return u, role


def test_dashboard_shows_no_pending_items_message_when_empty(client, db):
    _login(client, db, username="nobody_pending")
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Nothing waiting for your action" in resp.data


def test_dashboard_shows_pending_task_with_clickable_link(client, db):
    approver, approver_role = _login(client, db, username="approver1",
                                     codes=["tripticket.view"])
    branch = BranchService().create(code="BR-DASH", name="Dash Branch")
    vt = VehicleTypeService().create(code="LV-DASH", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="DASH-000")
    driver = DriverService().create(
        employee_number="EMP-DASH1", first_name="Ana", last_name="Reyes",
        license_number="LIC-DASH1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    requester = User(username="dash_requester", email="dashreq@x.com",
                     password_hash="x", first_name="Rico", last_name="Santos")
    db.session.add(requester)
    db.session.commit()

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Dash Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Baguio",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=requester)
    TripTicketService().submit(trip.id, user=requester)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"For My Action" in resp.data
    assert trip.document_number.encode() in resp.data
    assert b"Rico Santos" in resp.data  # shows full_name, not raw username
    assert f"/transactions/trip-tickets/{trip.id}".encode() in resp.data


def test_dashboard_does_not_show_task_outside_users_scope(client, db):
    from app.modules.user_management.org_scope_service import UserOrgScopeService
    approver, approver_role = _login(client, db, username="scoped_approver",
                                     codes=["tripticket.view"])
    branch_mine = BranchService().create(code="BR-MINE", name="My Branch")
    branch_other = BranchService().create(code="BR-OTHER", name="Other Branch")
    UserOrgScopeService().assign(approver.id, scope_type="BRANCH",
                                branch_id=branch_mine.id)

    vt = VehicleTypeService().create(code="LV-SCOPE2", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=branch_other.id, conduction_number="SCOPE-000")
    driver = DriverService().create(
        employee_number="EMP-SCOPE1", first_name="Ben", last_name="Cruz",
        license_number="LIC-SCOPE1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch_other.id)
    requester = User(username="scope_requester", email="scopereq@x.com",
                     password_hash="x")
    db.session.add(requester)
    db.session.commit()

    dt = DocumentTypeService().create(code="TT2", name="Trip Ticket 2",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT2",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Scope Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    class _FakeService(TripTicketService):
        document_type_code = "TT2"

    trip = _FakeService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Davao",
        purpose="Transfer", departure_datetime=datetime(2026, 7, 21, 8, 0),
        odometer_out=2000, user=requester)
    _FakeService().submit(trip.id, user=requester)

    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Nothing waiting for your action" in resp.data
    assert trip.document_number.encode() not in resp.data
