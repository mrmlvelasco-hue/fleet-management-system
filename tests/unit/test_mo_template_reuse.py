"""Save as Template -- the saved template must be reusable.

"Save as Template" turns an order's Scope of Work into a PM Scope
Template with no PM schedule attached. The New MO form only listed
templates reachable through the vehicle's PM schedules, so a saved
template existed in Configuration but could never be picked when
creating an order. Likewise the "default scope template" set on a
Transaction Type was stored and never read.

Fixed here (server side):
  * new-prefill offers schedule-less ("generic") templates of the
    chosen Maintenance Type, alongside the schedule-linked ones;
  * GET /mo-transaction-types carries default_scope_template_id so the
    form can pre-fill the scope when that Transaction Type is chosen.
"""
import json
from datetime import date

import pytest

from app.cli import _seed_maintenance_classes, _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.maintenance_config.models import PMScopeItem, PMScopeTemplate
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, TransactionTypeService)
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
    _seed_maintenance_classes()
    _ensure_doc_type("MO")
    db.session.commit()
    branch = BranchService().create(code="BR-TR", name="Template Reuse Branch")
    vt = VehicleTypeService().create(code="LV-TR", name="Light",
                                     category="LIGHT")
    nor = MaintenanceTypeService().create(code="TR-NOR", name="Normal Work",
                                          category="CM")
    other = MaintenanceTypeService().create(code="TR-OTH", name="Other Work",
                                            category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="TR-001")
    # A second vehicle: one vehicle may hold only one open order per
    # maintenance type, and _saved_template() already opens one.
    vehicle2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2022,
        branch_id=branch.id, conduction_number="TR-002")
    role = Role(name="tr-role")
    role.permissions = Permission.query.all()
    user = User(username="truser", email="tr@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"vehicle": vehicle, "vehicle2": vehicle2, "nor": nor,
            "other": other}


def _hdr(client):
    r = client.post("/api/v1/auth/token",
                    json={"username": "truser", "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def _saved_template(env, name="Repainting - standard"):
    """The real path: build an order's scope by hand, then Save as Template."""
    order = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, maintenance_type_id=env["nor"].id,
        scheduled_date=date.today(), user=None,
        scope_items=[
            {"activity_code": "PNT-01", "activity_description": "Sand body"},
            {"activity_description": "Mask windows"}])
    return MaintenanceOrderService().save_scope_as_template(
        order.id, name=name)


def _prefill(client, env, maintenance_type_id=None):
    url = ("/api/v1/maintenance-orders/new-prefill"
           f"?vehicle_id={env['vehicle'].id}")
    if maintenance_type_id:
        url += f"&maintenance_type_id={maintenance_type_id}"
    r = client.get(url, headers=_hdr(client))
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


# ── the saved template is offered on the New MO form ────────────────────

def test_saved_template_is_offered_for_its_maintenance_type(client, db, env):
    tmpl = _saved_template(env)
    ids = [t["id"] for t in
           _prefill(client, env, env["nor"].id)["scope_templates"]]
    assert tmpl.id in ids


def test_saved_template_is_not_offered_for_a_different_type(client, db, env):
    tmpl = _saved_template(env)
    ids = [t["id"] for t in
           _prefill(client, env, env["other"].id)["scope_templates"]]
    assert tmpl.id not in ids


def test_generic_templates_stay_out_until_a_type_is_chosen(client, db, env):
    """No Maintenance Type yet (vehicle picked first): keep the old
    behaviour -- only the vehicle's own PM-schedule templates."""
    tmpl = _saved_template(env)
    ids = [t["id"] for t in _prefill(client, env)["scope_templates"]]
    assert tmpl.id not in ids


def test_inactive_saved_template_is_not_offered(client, db, env):
    tmpl = _saved_template(env)
    tmpl.is_active = False
    db.session.commit()
    ids = [t["id"] for t in
           _prefill(client, env, env["nor"].id)["scope_templates"]]
    assert tmpl.id not in ids


def test_an_order_can_be_created_from_the_saved_template(db, env):
    tmpl = _saved_template(env)
    order = MaintenanceOrderService().create(
        vehicle_id=env["vehicle2"].id, maintenance_type_id=env["nor"].id,
        scheduled_date=date.today(), user=None,
        scope_template_id=tmpl.id)
    assert [i.activity_description for i in order.checklist_items] == [
        "Sand body", "Mask windows"]
    assert order.checklist_items[1].activity_code is None


def test_scope_template_details_returns_the_saved_lines(client, db, env):
    tmpl = _saved_template(env)
    r = client.get("/api/v1/maintenance-orders/scope-template-details"
                   f"?template_id={tmpl.id}", headers=_hdr(client))
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert [i["activity_description"] for i in items] == [
        "Sand body", "Mask windows"]


# ── Transaction Type default is exposed to the form ─────────────────────

def _tt(code):
    return TransactionType.query.filter_by(code=code).first()


def test_dropdown_carries_the_default_scope_template_id(client, db, env):
    tmpl = _saved_template(env)
    TransactionTypeService().update(_tt("MAINT-REPAINT").id,
                                    default_scope_template_id=tmpl.id)
    r = client.get("/api/v1/mo-transaction-types", headers=_hdr(client))
    by_code = {t["code"]: t for t in r.get_json()["items"]}
    assert by_code["MAINT-REPAINT"]["default_scope_template_id"] == tmpl.id
    assert by_code["MAINT-REPAIR"]["default_scope_template_id"] is None


def test_dropdown_hides_a_deactivated_default(client, db, env):
    """An inactive template must not be pre-filled onto new orders."""
    tmpl = _saved_template(env)
    TransactionTypeService().update(_tt("MAINT-REPAINT").id,
                                    default_scope_template_id=tmpl.id)
    tmpl.is_active = False
    db.session.commit()
    r = client.get("/api/v1/mo-transaction-types", headers=_hdr(client))
    by_code = {t["code"]: t for t in r.get_json()["items"]}
    assert by_code["MAINT-REPAINT"]["default_scope_template_id"] is None
