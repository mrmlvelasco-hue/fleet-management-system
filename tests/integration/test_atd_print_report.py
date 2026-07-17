from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.atd.service import ATDService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="ATDPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="atd_print_user", email="atd_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "atd_print_user", "password": "pw123456"})
    return u


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-ATDPRINT", name="ATD Print Branch")
    vt = VehicleTypeService().create(code="LV-ATDPRINT", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="NKR 60", year=2024,
        branch_id=branch.id, conduction_number="ATDPRINT-000",
        plate_number="WJR-408")
    driver = DriverService().create(
        employee_number="EMP-ATDPRINT1", first_name="Alwin", last_name="Delo Santos",
        license_number="LIC-ATDPRINT1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    return branch, vt, vehicle, driver


def test_print_shows_gate_guard_salutation_and_authorization_title(client, db, env):
    branch, vt, vehicle, driver = env
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Assigned unit is due for preventive maintenance.",
        valid_from=date.today(), valid_to=date.today(),
        odometer_out=0, user=None)
    _login(client, db, codes=["atd.view", "atd.print"])
    resp = client.get(f"/transactions/atd/{atd.id}/print")
    assert resp.status_code == 200
    assert b"To Gate Guard:" in resp.data
    assert b"AUTHORIZATION" in resp.data
    assert b"Alwin Delo Santos" in resp.data
    assert b"WJR-408" in resp.data
    assert b"Isuzu / NKR 60" in resp.data


def test_print_shows_wo_number_when_linked_to_maintenance_order(client, db, env):
    branch, vt, vehicle, driver = env
    mt = MaintenanceTypeService().create(code="ATDPRINT-MT", name="ATD Print Test MT",
                                         category="PM")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)

    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id,
        purpose="Assigned unit is due for preventive maintenance.",
        valid_from=date.today(), valid_to=date.today(),
        maintenance_order_id=order.id, user=None)
    _login(client, db, codes=["atd.view", "atd.print"])
    resp = client.get(f"/transactions/atd/{atd.id}/print")
    assert resp.status_code == 200
    assert b"with WO no." in resp.data
    assert order.document_number.encode() in resp.data


def test_print_shows_two_copies(client, db, env):
    branch, vt, vehicle, driver = env
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, purpose="General errand",
        valid_from=date.today(), valid_to=date.today(), user=None)
    _login(client, db, codes=["atd.view", "atd.print"])
    resp = client.get(f"/transactions/atd/{atd.id}/print")
    assert resp.status_code == 200
    assert resp.data.count(b"Page : 1 of 2") == 1
    assert resp.data.count(b"Page : 2 of 2") == 1


def test_print_shows_multiple_real_approver_signatures(client, db, env):
    branch, vt, vehicle, driver = env
    role = Role(name="ATDPrintApproverRole")
    for code in ["atd.view", "atd.update", "atd.print"]:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    approver = User(username="atd_print_approver", email="atd_print_approver@x.com",
                    password_hash=hash_password("pw123456"), first_name="Judel",
                    last_name="Bernabe")
    approver.roles.append(role)
    db.session.add_all([role, approver])
    db.session.commit()

    DocumentTypeService().create(code="ATD", name="Authority To Drive",
                                 requires_approval=True, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="ATD").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="ATD",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    path = ApprovalPathService().create(name="ATD Print Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    requester = _login(client, db, codes=["atd.view", "atd.create", "atd.update",
                                          "atd.print"])
    atd = ATDService().create(
        vehicle_id=vehicle.id, driver_id=driver.id, purpose="General errand",
        valid_from=date.today(), valid_to=date.today(), user=requester)
    ATDService().submit(atd.id, user=requester)
    client.get("/logout")
    client.post("/login", data={"username": "atd_print_approver", "password": "pw123456"})
    client.post(f"/transactions/atd/{atd.id}/approve")

    resp = client.get(f"/transactions/atd/{atd.id}/print")
    assert resp.status_code == 200
    assert b"Judel Bernabe" in resp.data
    assert requester.full_name.encode() in resp.data
