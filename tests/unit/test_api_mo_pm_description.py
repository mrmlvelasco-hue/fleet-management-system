"""Maintenance Order detail API -- the PMS description reaches the print resolved.

Flask's own MO print renders two PMS description blocks with the
pm2..pm9 tokens substituted against the order's vehicle: "Work Order
Template (PM)" (the linked schedule's work_description_template) and
"Work Description" (the order's own description). The React print got
the checklist lines resolved by the API, but printed the description raw
-- so a literal "pm2 pm3 with Plate no. pm4" could reach paper -- and had
no Work Order Template block at all.

Two additive, read-only fields fix that without touching anything that
is stored:

    description_resolved   the order's description, tokens resolved
    pm_work_description    the linked schedule's template, tokens resolved

`description` itself stays exactly as stored: the edit form round-trips
it, and silently rewriting saved text on a read is not this endpoint's job.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User

TEMPLATE = "10,000 km servicing of pm2 pm3 with Plate no. pm4"


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()
    role = Role(name="MO Print Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "maintenanceorder.view").all()
    user = User(username="printuser", email="pu@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    branch = BranchService().create(code="BR-PD", name="Print Branch")
    vt = VehicleTypeService().create(code="LV-PD", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="PD-MT", name="PMS", category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="CDN-907")
    for code in ["MO"]:
        if DocumentType.query.filter_by(code=code).first() is None:
            DocumentTypeService().create(code=code, name=code,
                                         requires_approval=False,
                                         auto_numbering=True)
            dt = DocumentType.query.filter_by(code=code).first()
            NumberingSchemeService().create(
                document_type_id=dt.id, prefix=code, include_year=True,
                digit_count=6, reset_policy="YEARLY")
    schedule = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        work_description_template=TEMPLATE)
    db.session.commit()
    return {"vehicle": vehicle, "mt": mt, "schedule": schedule}


def _token(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "printuser", "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _detail(client, oid):
    r = client.get(f"/api/v1/maintenance-orders/{oid}",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def _order(db, env, *, schedule=True, description=None):
    order = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, maintenance_type_id=env["mt"].id,
        pm_schedule_id=env["schedule"].id if schedule else None,
        description=description, scheduled_date=date.today(), user=None)
    db.session.commit()
    return order


def test_description_is_resolved_for_print_but_stored_text_is_untouched(
        db, client, env):
    order = _order(db, env, description="Service pm2 pm3, plate pm4")
    status, body = _detail(client, order.id)
    assert status == 200
    # The stored text is what the edit form round-trips -- never rewritten.
    assert body["description"] == "Service pm2 pm3, plate pm4"
    assert body["description_resolved"] == "Service Toyota Vios, plate CDN-907"


def test_linked_schedule_template_is_exposed_resolved(db, client, env):
    order = _order(db, env, description="anything")
    _status, body = _detail(client, order.id)
    assert body["pm_work_description"] == (
        "10,000 km servicing of Toyota Vios with Plate no. CDN-907")


def test_an_order_with_no_pm_schedule_has_no_template_block(db, client, env):
    order = _order(db, env, schedule=False, description="Plain note")
    _status, body = _detail(client, order.id)
    assert body["pm_work_description"] is None
    assert body["description_resolved"] == "Plain note"


def test_an_order_with_no_description_resolves_to_none_not_an_error(
        db, client, env):
    order = _order(db, env, description=None)
    status, body = _detail(client, order.id)
    assert status == 200
    assert body["description_resolved"] is None
