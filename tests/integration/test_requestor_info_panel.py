from datetime import date, datetime

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService, DepartmentService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="RequestorInfoRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="viewer_requestor", email="viewer_req@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "viewer_requestor", "password": "pw123456"})
    return u


def test_tripticket_detail_shows_requestor_info(client, db):
    _login(client, db, codes=["tripticket.view", "tripticket.create"])
    branch = BranchService().create(code="BR-REQINFO", name="ReqInfo Branch")
    dept = DepartmentService().create(code="DEPT-REQINFO", name="Fleet Ops",
                                      branch_id=branch.id)
    vt = VehicleTypeService().create(code="LV-REQINFO", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Wigo", year=2024,
        branch_id=branch.id, conduction_number="REQINFO-000")
    driver = DriverService().create(
        employee_number="EMP-REQ1", first_name="Nora", last_name="Villar",
        license_number="LIC-REQ1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)

    requester = User(username="req_creator", email="req_creator@x.com",
                     password_hash=hash_password("pw123456"),
                     first_name="Ella", last_name="Marquez",
                     employee_id="EMP-9001", branch_id=branch.id,
                     department_id=dept.id)
    db.session.add(requester)
    db.session.commit()

    dt = DocumentTypeService().create(code="TT", name="Trip Ticket",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    from app.modules.transactions.trip_ticket.service import TripTicketService
    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Tagaytay",
        purpose="Site visit", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=requester)

    resp = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert resp.status_code == 200
    assert b"Requestor Information" in resp.data
    assert b"Ella Marquez" in resp.data
    assert b"EMP-9001" in resp.data
    assert b"Fleet Ops" in resp.data
    assert b"ReqInfo Branch" in resp.data


def test_purchaserequest_detail_shows_requestor_info(client, db):
    _login(client, db, codes=["purchaserequest.view", "purchaserequest.create"])
    branch = BranchService().create(code="BR-REQINFO2", name="ReqInfo Branch 2")
    requester = User(username="req_creator2", email="req_creator2@x.com",
                     password_hash=hash_password("pw123456"),
                     first_name="Marco", last_name="Reyes")
    db.session.add(requester)
    db.session.commit()

    DocumentTypeService().create(code="PR", name="Purchase Request",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.transactions.purchase_request.service import (
        PurchaseRequestService)
    pr = PurchaseRequestService().create(
        description="Office supplies", user=requester, lines=[
            {"item_description": "Paper", "quantity": 5, "unit_cost": 100}])

    resp = client.get(f"/transactions/purchase-requests/{pr.id}")
    assert resp.status_code == 200
    assert b"Requestor Information" in resp.data
    assert b"Marco Reyes" in resp.data
