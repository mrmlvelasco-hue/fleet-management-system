from datetime import date

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService


def _login(client, db, *, codes=()):
    role = Role(name="OdoPrintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="odo_print_user", email="odo_print_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "odo_print_user", "password": "pw123456"})
    return u


def test_print_shows_vehicle_current_odometer_even_when_this_mo_left_it_blank(client, db):
    """Reproduces the reported finding: a vehicle with real odometer
    history (from another transaction) still showed a blank Odometer
    field on THIS Maintenance Order's print report, because it was only
    displaying this specific order's own (unset) odometer_at_service
    rather than the vehicle's actual current reading."""
    branch = BranchService().create(code="BR-ODOPRINT", name="Odo Print Branch")
    vt = VehicleTypeService().create(code="LV-ODOPRINT", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="ODOPRINT-MT", name="Odo Print Test",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2024,
        branch_id=branch.id, conduction_number="ODOPRINT-000",
        current_odometer=15750)  # set by an earlier, separate transaction
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)  # no odometer_at_service given
    order.status = "COMPLETED"
    order.completed_date = date.today()
    db.session.commit()

    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.print"])
    resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert resp.status_code == 200
    assert b"15,750" in resp.data
