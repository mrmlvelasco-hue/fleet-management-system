"""API-level wiring for Vehicle Master Data Update Mapping (2026-11-02).

1. GET /mo-transaction-types carries `vehicle_mapping` so the New MO
   form knows, per type, whether to show the Vehicle Master Data
   Change panel without a second round trip.
2. GET /maintenance-orders/new-prefill carries `vehicle.vehicle_fields`
   (every whitelisted field's CURRENT value) so the panel can show the
   "From" side as soon as a vehicle is picked.
3. POST /maintenance-orders accepts `vehicle_changes` and the created
   order's detail response carries both `vehicle_mapping` and
   `vehicle_changes`.
4. GET /mo-transaction-types/vehicle-field-whitelist lists the allowed
   fields for the admin config dropdown.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    TransactionTypeService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    sync_permissions()
    branch = BranchService().create(code="BR-VMDAPI", name="VMD API Branch")
    vt = VehicleTypeService().create(code="LV-VMDAPI", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="VMDAPI-000", color="White")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    tt = TransactionTypeService().create(
        code="ADM-CHG-COLOR-API", name="Change Color",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Vehicle Color",
        input_type="TEXT", is_required=True)
    role = Role(name="vmdapi-role")
    role.permissions = Permission.query.all()
    user = User(username="vmdapiuser", email="vmdapi@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"vehicle": vehicle, "tt": tt, "branch": branch}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "vmdapiuser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def test_dropdown_carries_vehicle_mapping_for_a_configured_type(client, db, env):
    hdr = _hdr(client)
    r = client.get("/api/v1/mo-transaction-types", headers=hdr)
    assert r.status_code == 200
    rows = json.loads(r.get_data(as_text=True))["items"]
    row = next(x for x in rows if x["id"] == env["tt"].id)
    assert row["vehicle_mapping"]["target_field"] == "color"
    assert row["vehicle_mapping"]["display_label"] == "New Vehicle Color"


def test_dropdown_carries_none_for_an_unconfigured_type(client, db, env):
    hdr = _hdr(client)
    tt2 = TransactionTypeService().create(
        code="ADM-CHG-OWNER-API", name="Change of Ownership",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    r = client.get("/api/v1/mo-transaction-types", headers=hdr)
    rows = json.loads(r.get_data(as_text=True))["items"]
    row = next(x for x in rows if x["id"] == tt2.id)
    assert row["vehicle_mapping"] is None


def test_new_prefill_carries_the_vehicle_s_current_whitelisted_values(client, db, env):
    hdr = _hdr(client)
    r = client.get(
        f"/api/v1/maintenance-orders/new-prefill?vehicle_id={env['vehicle'].id}",
        headers=hdr)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["vehicle"]["vehicle_fields"]["color"] == "White"
    assert "engine_number" in body["vehicle"]["vehicle_fields"]


def test_vehicle_field_whitelist_endpoint_lists_allowed_fields(client, db, env):
    hdr = _hdr(client)
    r = client.get("/api/v1/mo-transaction-types/vehicle-field-whitelist",
                   headers=hdr)
    assert r.status_code == 200
    values = [i["value"] for i in json.loads(r.get_data(as_text=True))["items"]]
    assert "color" in values


def test_create_order_with_vehicle_changes_via_api(client, db, env):
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "order_category": "OPERATIONAL",
        "transaction_type_id": env["tt"].id,
        "vehicle_changes": [{"target_field": "color", "new_value": "Red"}],
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["vehicle_mapping"]["target_field"] == "color"
    assert len(body["vehicle_changes"]) == 1
    assert body["vehicle_changes"][0]["new_value"] == "Red"
    assert body["vehicle_changes"][0]["status"] == "PENDING"


def test_get_single_mo_transaction_type_admin_record(client, db, env):
    """Reported 2026-11-02 UAT: the MO Transaction Types edit screen
    showed "Unable to load this record" -- the generic SimpleMasterForm
    loader does GET /mo-transaction-types/admin/<id> on every edit, but
    that single-record route never existed for this entity (only the
    list GET, create POST, and update PUT/PATCH), unlike every other
    admin master (branches, etc.), which all have it. 405, not 404, so
    the client was swallowing it as "Unable to load this record" rather
    than a clean not-found."""
    hdr = _hdr(client)
    r = client.get(f"/api/v1/mo-transaction-types/admin/{env['tt'].id}",
                   headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["id"] == env["tt"].id
    assert body["code"] == "ADM-CHG-COLOR-API"
    # Bonus: now carries the same vehicle_mapping as the list/dropdown.
    assert body["vehicle_mapping"]["target_field"] == "color"


def test_get_single_mo_transaction_type_admin_record_404s_for_unknown_id(client, db, env):
    hdr = _hdr(client)
    r = client.get("/api/v1/mo-transaction-types/admin/999999", headers=hdr)
    assert r.status_code == 404


def test_create_order_rejects_an_unwhitelisted_target_field_via_api(client, db, env):
    hdr = _hdr(client)
    r = client.post("/api/v1/maintenance-orders", headers=hdr, json={
        "vehicle_id": env["vehicle"].id,
        "scheduled_date": "2026-11-02",
        "order_category": "OPERATIONAL",
        "transaction_type_id": env["tt"].id,
        "vehicle_changes": [{"target_field": "plate_number",
                             "new_value": "ABC-123"}],
    })
    assert r.status_code == 400
