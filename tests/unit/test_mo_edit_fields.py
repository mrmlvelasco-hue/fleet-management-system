"""Document edit policy, Phase 1: every field the MO was created with
can be corrected, and the corrections are validated.

Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md

The trigger was MO-2026-000046, a Transfer raised with Origin and
Destination both Muntinlupa: the service accepted destination_branch_id
on update, but PATCH /maintenance-orders/<id> silently dropped it (and
driver_id, disposal_value, disposal_recipient, scope_template_id,
vehicle_id), answering 200 while nothing changed.
"""
import json
from datetime import date
from decimal import Decimal

import pytest

from app.cli import _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, TransactionType)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, InvalidOrderCategoryError,
    InvalidOrderStateError)
from app.modules.user_management.models import Permission, Role, User
from tests.conftest import tiny_jpeg_file


def _doc(code):
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=4,
                                        reset_policy="YEARLY")


def _tt(code):
    return TransactionType.query.filter_by(code=code).first()


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    for code in ("MO", "ATR", "ADR"):
        _doc(code)
    manila = BranchService().create(code="BR-MNL", name="Manila Hub")
    munti = BranchService().create(code="BR-MUN", name="Muntinlupa")
    cebu = BranchService().create(code="BR-CEB", name="Cebu Branch")
    vt = VehicleTypeService().create(code="LV-EF", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-EF", name="PMS",
                                         category="PREVENTIVE")
    mt2 = MaintenanceTypeService().create(code="CM-EF", name="Repair",
                                          category="CM")
    vios = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2012,
        branch_id=manila.id, conduction_number="TIL-120")
    dmax = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="D-Max", year=2019,
        branch_id=manila.id, conduction_number="NAB-4471")
    driver = DriverService().create(
        employee_number="EMP-EF1", first_name="Maria", last_name="Santos",
        license_number="LIC-EF1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=manila.id,
        photo_file=tiny_jpeg_file())
    vendor = VendorService().create(code="V-EF", name="Toyota Alabang")
    role = Role(name="ef-role")
    role.permissions = Permission.query.filter(Permission.code.in_(
        ["maintenanceorder.view", "maintenanceorder.update"])).all()
    user = User(username="ef_user", email="ef@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return dict(manila=manila, munti=munti, cebu=cebu, mt=mt, mt2=mt2,
                vios=vios, dmax=dmax, driver=driver, vendor=vendor, user=user)


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "ef_user", "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}


def _transfer(env, dest=None):
    return MaintenanceOrderService().create(
        vehicle_id=env["vios"].id, order_category="OPERATIONAL",
        transaction_type_id=_tt("DEP-TRANSFER").id,
        destination_branch_id=(dest or env["munti"]).id,
        scheduled_date=date.today(), user=env["user"])


def _disposal(env):
    return MaintenanceOrderService().create(
        vehicle_id=env["vios"].id, order_category="OPERATIONAL",
        transaction_type_id=_tt("DIS-SCRAPPAGE").id,
        scheduled_date=date.today(), user=env["user"])


def _maint(env, vehicle=None, mt=None):
    return MaintenanceOrderService().create(
        vehicle_id=(vehicle or env["vios"]).id,
        maintenance_type_id=(mt or env["mt"]).id,
        scheduled_date=date.today(), user=env["user"])


def _patch(client, oid, body):
    return client.patch(f"/api/v1/maintenance-orders/{oid}", json=body,
                        headers=_hdr(client))


def _reload(oid):
    from app.extensions import db
    db.session.expire_all()
    return db.session.get(MaintenanceOrder, oid)


# ── every create-time field reaches the order through the API ───────

def test_transfer_destination_driver_and_classification_save(
        client, db, env):
    o = _transfer(env)
    r = _patch(client, o.id, {"destination_branch_id": env["cebu"].id,
                              "driver_id": env["driver"].id,
                              "assignment_classification": "PERK"})
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _reload(o.id)
    assert o.destination_branch_id == env["cebu"].id
    assert o.driver_id == env["driver"].id
    assert o.assignment_classification == "PERK"


def test_disposal_value_and_recipient_save(client, db, env):
    o = _disposal(env)
    r = _patch(client, o.id, {"disposal_value": "75,000.50",
                              "disposal_recipient": "Scrap Dealer Co."})
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _reload(o.id)
    assert o.disposal_value == Decimal("75000.50")
    assert o.disposal_recipient == "Scrap Dealer Co."


def test_maintenance_vehicle_vendor_odometer_save(client, db, env):
    o = _maint(env)
    r = _patch(client, o.id, {"vehicle_id": env["dmax"].id,
                              "vendor_id": env["vendor"].id,
                              "odometer_at_service": 20150})
    assert r.status_code == 200, r.get_data(as_text=True)
    o = _reload(o.id)
    assert o.vehicle_id == env["dmax"].id
    assert o.vendor_id == env["vendor"].id
    assert o.odometer_at_service == 20150


def test_bad_id_is_a_field_named_validation_error(client, db, env):
    o = _transfer(env)
    r = _patch(client, o.id, {"driver_id": "abc"})
    assert r.status_code == 400
    assert "driver_id" in r.get_json()["fields"]


def test_bad_disposal_value_is_a_field_named_validation_error(
        client, db, env):
    r = _patch(client, _disposal(env).id, {"disposal_value": "lots"})
    assert r.status_code == 400
    assert "disposal_value" in r.get_json()["fields"]


def test_clearing_driver_with_null_is_allowed(client, db, env):
    o = _transfer(env)
    _patch(client, o.id, {"driver_id": env["driver"].id})
    r = _patch(client, o.id, {"driver_id": None})
    assert r.status_code == 200
    assert _reload(o.id).driver_id is None


def test_changing_maintenance_type_updates_its_category(db, env):
    o = _maint(env)
    MaintenanceOrderService().update(o.id, user=env["user"],
                                     maintenance_type_id=env["mt2"].id)
    assert _reload(o.id).category == "CM"


# ── transaction type stays inside its group ─────────────────────────

def test_type_can_change_within_its_group(db, env):
    o = _disposal(env)
    MaintenanceOrderService().update(
        o.id, user=env["user"], transaction_type_id=_tt("DIS-SOLD").id)
    assert _reload(o.id).transaction_type.code == "DIS-SOLD"


def test_type_cannot_change_to_another_group(db, env):
    o = _transfer(env)
    with pytest.raises(InvalidOrderCategoryError) as exc:
        MaintenanceOrderService().update(
            o.id, user=env["user"], transaction_type_id=_tt("DIS-SOLD").id)
    assert "group" in str(exc.value).lower()
    assert _reload(o.id).transaction_type.code == "DEP-TRANSFER"


def test_cross_group_change_is_a_400_on_the_api(client, db, env):
    o = _transfer(env)
    r = _patch(client, o.id, {"transaction_type_id": _tt("DIS-SOLD").id})
    assert r.status_code == 400
    assert "transaction_type_id" in r.get_json()["fields"]


# ── origin and destination ──────────────────────────────────────────

def test_destination_equal_to_origin_is_rejected_on_edit(client, db, env):
    o = _transfer(env, dest=env["cebu"])
    r = _patch(client, o.id, {"destination_branch_id": env["manila"].id})
    assert r.status_code == 400
    assert "destination_branch_id" in r.get_json()["fields"]
    assert _reload(o.id).destination_branch_id == env["cebu"].id


def test_destination_equal_to_origin_is_rejected_on_create(db, env):
    with pytest.raises(InvalidOrderCategoryError):
        _transfer(env, dest=env["manila"])


def test_origin_follows_the_vehicle_when_the_order_is_edited(db, env):
    """MO-2026-000046: origin was snapshotted at creation and went stale
    when the vehicle moved before the transfer was done."""
    o = _transfer(env, dest=env["cebu"])
    env["vios"].branch_id = env["munti"].id
    db.session.commit()
    MaintenanceOrderService().update(o.id, user=env["user"],
                                     description="corrected")
    assert _reload(o.id).origin_branch_id == env["munti"].id


def test_origin_is_retaken_at_completion_before_the_vehicle_moves(db, env):
    o = _transfer(env, dest=env["cebu"])
    env["vios"].branch_id = env["munti"].id
    db.session.commit()
    MaintenanceOrderService().complete(o.id, actual_cost=0,
                                       completed_date=date.today())
    o = _reload(o.id)
    assert o.origin_branch_id == env["munti"].id
    assert db.session.get(Vehicle, env["vios"].id).branch_id == env["cebu"].id


def test_recompleting_does_not_overwrite_origin_with_destination(db, env):
    o = _transfer(env, dest=env["cebu"])
    svc = MaintenanceOrderService()
    svc.complete(o.id, actual_cost=0, completed_date=date.today())
    svc.complete(o.id, actual_cost=0, completed_date=date.today())
    assert _reload(o.id).origin_branch_id == env["manila"].id


def test_adding_a_destination_on_edit_sets_the_origin(db, env):
    o = MaintenanceOrderService().create(
        vehicle_id=env["vios"].id, order_category="OPERATIONAL",
        transaction_type_id=_tt("DEP-RELOCATION").id,
        scheduled_date=date.today(), user=env["user"])
    assert o.origin_branch_id is None
    MaintenanceOrderService().update(o.id, user=env["user"],
                                     destination_branch_id=env["cebu"].id)
    assert _reload(o.id).origin_branch_id == env["manila"].id


# ── changing the vehicle ────────────────────────────────────────────

def test_vehicle_change_rechecks_for_an_open_order(db, env):
    _maint(env, vehicle=env["dmax"])          # D-Max already has an open PMS
    o = _maint(env)                            # Vios PMS
    with pytest.raises(InvalidOrderStateError) as exc:
        MaintenanceOrderService().update(o.id, user=env["user"],
                                         vehicle_id=env["dmax"].id)
    assert "already has an open" in str(exc.value)
    assert _reload(o.id).vehicle_id == env["vios"].id


def test_vehicle_change_to_a_free_vehicle_is_allowed(db, env):
    o = _maint(env)
    MaintenanceOrderService().update(o.id, user=env["user"],
                                     vehicle_id=env["dmax"].id)
    assert _reload(o.id).vehicle_id == env["dmax"].id


def test_vehicle_change_retakes_the_transfer_origin(db, env):
    env["dmax"].branch_id = env["munti"].id
    db.session.commit()
    o = _transfer(env, dest=env["cebu"])
    MaintenanceOrderService().update(o.id, user=env["user"],
                                     vehicle_id=env["dmax"].id)
    assert _reload(o.id).origin_branch_id == env["munti"].id


# ── parts once a Purchase Request exists ────────────────────────────

def test_parts_are_locked_once_a_purchase_request_exists(db, env):
    _doc("PR")
    o = _maint(env)
    svc = MaintenanceOrderService()
    part = svc.add_part(o.id, part_description="Brake pad")
    svc.generate_purchase_request(o.id, env["user"])
    with pytest.raises(InvalidOrderStateError) as exc:
        svc.add_part(o.id, part_description="Rotor")
    assert "purchase request" in str(exc.value).lower()
    with pytest.raises(InvalidOrderStateError):
        svc.remove_part(part.id)


def test_scheduled_date_is_parsed_on_edit(client, db, env):
    o = _maint(env)
    r = _patch(client, o.id, {"scheduled_date": "2026-11-03"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _reload(o.id).scheduled_date == date(2026, 11, 3)


def test_bad_scheduled_date_is_a_validation_error(client, db, env):
    r = _patch(client, _maint(env).id, {"scheduled_date": "next week"})
    assert r.status_code == 400
    assert "scheduled_date" in r.get_json()["fields"]


def test_resaving_the_same_vehicle_is_not_a_duplicate_of_itself(db, env):
    """The edit form re-posts every field on save, so an order whose
    vehicle and maintenance type did not change must not collide with
    ITSELF in the open-order check."""
    o = _maint(env)
    MaintenanceOrderService().update(
        o.id, user=env["user"], vehicle_id=env["vios"].id,
        maintenance_type_id=env["mt"].id, description="re-saved")
    assert _reload(o.id).description == "re-saved"
