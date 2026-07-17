from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.maintenance_config.service import PMScopeTemplateService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="TokenPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="token_print_user", email="token_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "token_print_user", "password": "pw123456"})
    return u


def test_reported_scenario_checklist_tokens_resolve_on_print(client, db):
    """Reproduces the exact reported bug: 'Last date replaced : pm9
    Reference W.O.# pm8' showing up as literal unresolved text in the
    printed Maintenance Order checklist."""
    branch = BranchService().create(code="BR-TOKENBUG", name="Token Bug Branch")
    vt = VehicleTypeService().create(code="LV-TOKENBUG", name="Light", category="LIGHT")
    driver = DriverService().create(
        employee_number="EMP-TOKENBUG1", first_name="Juan", last_name="Dela Cruz",
        license_number="LIC-TOKENBUG1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=branch.id)
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="HR-V", year=2024,
        branch_id=branch.id, conduction_number="TOKENBUG-000",
        plate_number="HRV001", assigned_driver_id=driver.id)
    mt = MaintenanceTypeService().create(code="TOKENBUG-MT", name="Battery Replacement",
                                         category="PM")
    for code in ["MO"]:
        DocumentTypeService().create(code=code, name=code, requires_approval=False,
                                     auto_numbering=True)
        from app.modules.document_config.models import DocumentType
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")

    prior_order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date(2026, 6, 10), user=None)
    prior_order.status = "COMPLETED"
    prior_order.completed_date = date(2026, 6, 15)
    db.session.commit()

    scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="Battery Replacement Checklist",
        items=[{
            "activity_code": "S04-00009-001",
            "activity_description": (
                "Validation and approval by fleet of battery replacement "
                "based on complaint, last date replaced and actual "
                "condition. Last date replaced : pm9 Reference W.O.# pm8"),
            "sort_order": 1,
        }])

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scope_template_id=scope.id, scheduled_date=date.today(), user=None)

    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    # The literal tokens must be gone...
    assert b"pm9" not in resp.data
    assert b"pm8" not in resp.data
    # ...replaced with the real values.
    assert prior_order.document_number.encode() in resp.data
    assert b"2026-06-15" in resp.data
