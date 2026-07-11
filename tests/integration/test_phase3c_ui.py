from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="Phase3cRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="quinn", email="quinn@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "quinn", "password": "pw123456"})
    return u


def _seed_pr_doctype(db):
    dt = DocumentTypeService().create(code="PR", name="Purchase Request",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="PR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")


def _seed_vr_doctype(db):
    dt = DocumentTypeService().create(code="VR", name="Vehicle Registration",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")


def _seed_vehicle(db):
    branch = BranchService().create(code="BR-3C", name="3C Branch")
    vt = VehicleTypeService().create(code="LV-3C", name="Light", category="LIGHT")
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2026,
        branch_id=branch.id, conduction_number="3C-000")


# ---------- Purchase Request UI ----------

def test_purchaserequest_list_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/transactions/purchase-requests").status_code == 403


def test_purchaserequest_list_and_new_render(client, db):
    _login(client, db, codes=["purchaserequest.view", "purchaserequest.create"])
    assert client.get("/transactions/purchase-requests").status_code == 200
    assert client.get("/transactions/purchase-requests/new").status_code == 200


def test_purchaserequest_full_flow_with_lines(client, db):
    user = _login(client, db, codes=[
        "purchaserequest.view", "purchaserequest.create",
        "purchaserequest.update", "purchaserequest.print"])
    _seed_pr_doctype(db)

    resp = client.post("/transactions/purchase-requests/new", data={
        "description": "Office Supplies",
        "item_description": ["Paper", "Pens"],
        "quantity": ["10", "20"],
        "unit_cost": ["50", "15"],
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest)
    pr = PurchaseRequest.query.first()
    assert pr is not None
    assert pr.amount == 800

    client.post(f"/transactions/purchase-requests/{pr.id}/submit",
               follow_redirects=True)
    client.post(f"/transactions/purchase-requests/{pr.id}/mark-ordered",
               follow_redirects=True)
    client.post(f"/transactions/purchase-requests/{pr.id}/mark-received",
               follow_redirects=True)
    assert db.session.get(PurchaseRequest, pr.id).status == "RECEIVED"

    print_resp = client.get(f"/transactions/purchase-requests/{pr.id}/print")
    assert print_resp.status_code == 200
    assert b"PURCHASE REQUEST" in print_resp.data


# ---------- Vehicle Registration UI ----------

def test_vehicleregistration_list_and_new_render(client, db):
    _login(client, db, codes=[
        "vehicleregistration.view", "vehicleregistration.create"])
    assert client.get("/transactions/vehicle-registrations").status_code == 200
    assert client.get("/transactions/vehicle-registrations/new").status_code == 200


def test_vehicleregistration_full_flow_assigns_plate(client, db):
    user = _login(client, db, codes=[
        "vehicleregistration.view", "vehicleregistration.create",
        "vehicleregistration.update", "vehicleregistration.print"])
    _seed_vr_doctype(db)
    vehicle = _seed_vehicle(db)

    resp = client.post("/transactions/vehicle-registrations/new", data={
        "vehicle_id": str(vehicle.id), "registration_type": "NEW",
        "registration_date": "2026-01-01",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    reg = VehicleRegistration.query.first()
    assert reg is not None
    assert reg.expiry_date == date(2029, 1, 1)

    client.post(f"/transactions/vehicle-registrations/{reg.id}/submit",
               follow_redirects=True)
    client.post(f"/transactions/vehicle-registrations/{reg.id}/complete",
               data={"or_number": "OR-999", "cr_number": "CR-999",
                     "plate_number": "NEW-001"}, follow_redirects=True)

    assert db.session.get(VehicleRegistration, reg.id).status == "COMPLETED"
    from app.modules.master_data.vehicle.models import Vehicle
    assert db.session.get(Vehicle, vehicle.id).plate_number == "NEW-001"

    print_resp = client.get(
        f"/transactions/vehicle-registrations/{reg.id}/print")
    assert print_resp.status_code == 200
    assert b"VEHICLE REGISTRATION" in print_resp.data


def test_vehicle_detail_shows_registration_history(client, db):
    user = _login(client, db, codes=[
        "vehicle.view", "vehicleregistration.view",
        "vehicleregistration.create", "vehicleregistration.update"])
    _seed_vr_doctype(db)
    vehicle = _seed_vehicle(db)

    from app.modules.transactions.vehicle_registration.service import (
        VehicleRegistrationService)
    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date(2026, 1, 1), user=user)

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert reg.document_number.encode() in resp.data
