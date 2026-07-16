from datetime import date, datetime

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.transactions.trip_ticket.service import TripTicketService


def test_cancel_button_only_shown_to_the_requester_not_other_approvers(client, db):
    """Reproduces the reported gap: the Cancel button used to be gated
    only by the generic '.update' permission, which an approver could
    easily also hold — violating 'Cancel shall not appear for normal
    approvers'. Now it's restricted to the record's own requester."""
    branch = BranchService().create(code="BR-CANCELUI", name="Cancel UI Branch")
    vt = VehicleTypeService().create(code="LV-CANCELUI", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="CANCELUI-000")
    driver = DriverService().create(
        employee_number="EMP-CANCELUI1", first_name="Rey", last_name="Santos",
        license_number="LIC-CANCELUI1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="TT").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="TT",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    role = Role(name="CancelUIRole")
    for code in ["tripticket.view", "tripticket.update"]:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    requester = User(username="cancelui_requester", email="cancelui_requester@x.com",
                     password_hash=hash_password("pw123456"))
    other_user = User(username="cancelui_other", email="cancelui_other@x.com",
                      password_hash=hash_password("pw123456"))
    requester.roles.append(role)
    other_user.roles.append(role)
    db.session.add_all([role, requester, other_user])
    db.session.commit()

    trip = TripTicketService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, destination="Baguio",
        purpose="Delivery", departure_datetime=datetime(2026, 7, 20, 8, 0),
        odometer_out=1000, user=requester)

    # The requester sees Cancel
    client.post("/login", data={"username": "cancelui_requester", "password": "pw123456"})
    resp = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert b">Cancel<" in resp.data
    client.get("/logout")

    # A different user with the SAME .update permission does NOT see Cancel
    client.post("/login", data={"username": "cancelui_other", "password": "pw123456"})
    resp2 = client.get(f"/transactions/trip-tickets/{trip.id}")
    assert b">Cancel<" not in resp2.data
