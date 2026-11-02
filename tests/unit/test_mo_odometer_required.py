"""Odometer at Service is a required field on MO creation (2026-11-02).

Michael: "to the MO creation kindly the Odo meter should be required
field you cannot proceed." Before this, odometer_at_service was an
optional column -- useful for scheduled PM generation (which always
has a real reading, from Vehicle.current_odometer) but silently
skippable on the manual New MO screen, where Michael wants every
order to carry a real reading at the moment it was raised.

Enforced at the API route (POST /maintenance-orders), the exact same
place vehicle_id is already required -- not inside
MaintenanceOrderService.create() itself, which stays usable by the PM
auto-generation Celery task and the checklist-triggered corrective-MO
path without this change reaching either of them. vehicle_id is
already required there too (confirmed below), so "every MO creation
requires a plate number" was already true going in.

0 is a legitimate odometer reading (a brand-new vehicle) and must not
be treated as "missing" -- the check is "was a number supplied",
not "is it truthy".
"""
import json

import pytest

from app.cli import _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    _ensure_doc_type("MO")
    branch = BranchService().create(code="BR-ODO", name="Odometer Branch")
    vt = VehicleTypeService().create(code="LV-ODO", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="ODO-CM", name="Corrective",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2023,
        branch_id=branch.id, conduction_number="ODO-001")
    op_tt = (TransactionType.query
             .filter_by(order_category="OPERATIONAL").first())
    role = Role(name="odo-role")
    role.permissions = Permission.query.all()
    user = User(username="odouser", email="odo@example.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    from app.extensions import db as _db
    _db.session.add_all([role, user])
    _db.session.commit()
    return {"vehicle": vehicle, "mt": mt, "op_tt": op_tt}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "odouser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_creation_is_refused_with_no_odometer_at_all(client, db, env):
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
    })
    assert r.status_code == 400, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["fields"]["odometer_at_service"]
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    assert MaintenanceOrder.query.count() == 0


def test_creation_is_refused_with_a_blank_odometer_string(client, db, env):
    """The New MO form sends "" for an untouched number field, not an
    absent key -- the same shape vehicle_id's own check already
    handles, so this check must handle it too."""
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
        "odometer_at_service": "",
    })
    assert r.status_code == 400
    assert "odometer_at_service" in json.loads(r.get_data(as_text=True))["fields"]


def test_a_zero_odometer_reading_is_accepted_not_treated_as_missing(
        client, db, env):
    """A brand-new vehicle genuinely reads 0 km -- that is real data,
    not an empty field."""
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
        "odometer_at_service": 0,
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    assert json.loads(r.get_data(as_text=True))["odometer_at_service"] == 0


def test_a_non_numeric_odometer_is_a_400_naming_the_field(client, db, env):
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
        "odometer_at_service": "not-a-number",
    })
    assert r.status_code == 400
    assert "odometer_at_service" in json.loads(r.get_data(as_text=True))["fields"]


def test_a_real_odometer_reading_proceeds(client, db, env):
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
        "odometer_at_service": 15230,
    })
    assert r.status_code == 201, r.get_data(as_text=True)


def test_odometer_is_required_for_operational_orders_too(client, db, env):
    """The New MO form shows the Odometer field for every category
    (Deployment, Disposal, Administrative, Accessories included) --
    the requirement must match, not apply only to MAINTENANCE orders."""
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "order_category": "OPERATIONAL",
        "transaction_type_id": env["op_tt"].id if env["op_tt"] else None,
    })
    assert r.status_code == 400
    assert "odometer_at_service" in json.loads(r.get_data(as_text=True))["fields"]


def test_vehicle_id_is_already_required_on_creation(client, db, env):
    """Michael: 'i think all in the MO creation has required the
    vehicle plate number' -- confirmed already true (vehicle_id, the
    field the plate-number picker actually submits, is validated
    before anything else). Locked in here as a regression guard, not
    a new rule."""
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "scheduled_date": "2026-11-02",
        "maintenance_type_id": env["mt"].id,
        "odometer_at_service": 1000,
    })
    assert r.status_code == 400
    assert "vehicle_id" in json.loads(r.get_data(as_text=True))["fields"]


def test_the_pm_auto_generation_task_is_unaffected(app, db, env):
    """This feature targets the manual New MO screen -- the Celery
    auto-generation path calls the SERVICE directly (never this API
    route) and always passes a real int (Vehicle.current_odometer,
    NOT NULL, default 0), so it must keep working exactly as before."""
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    order = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, maintenance_type_id=env["mt"].id,
        scheduled_date=__import__("datetime").date.today(),
        odometer_at_service=env["vehicle"].current_odometer, user=None)
    assert order.id is not None
