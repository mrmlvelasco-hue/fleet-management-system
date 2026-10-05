"""Phase B: Save as template from an MO's Scope of Work.

An order's edited scope can be promoted to a permanent PM Scope
Template in one click, so the same checklist can be reused on future
orders without retyping it.

Rules:
- Requires a maintenance_type_id on the order (MAINTENANCE category).
- OPERATIONAL orders have no maintenance_type so they cannot create a
  Maintenance-typed template; the endpoint rejects them with 400.
- The template name comes from the request (required).
- The template's items are copied from the ORDER's current checklist
  rows, not from any master template, so manual edits are preserved.
- Codes that are None are preserved as None in the template items
  (activity_code is now nullable in PMScopeItem).
- Permission: pmscopetemplate.create.
- Response: the new template's full JSON.
"""
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.maintenance_config.models import PMScopeItem, PMScopeTemplate
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import TransactionType
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User
import json


def _mk_doc(code):
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
    _mk_doc("MO")
    branch = BranchService().create(code="BR-SAT", name="Save-as-tmpl Branch")
    vt = VehicleTypeService().create(code="LV-SAT", name="Light",
                                      category="LIGHT")
    mt = MaintenanceTypeService().create(code="SAT-CM", name="Corrective",
                                          category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2022,
        branch_id=branch.id, conduction_number="SAT-001")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None,
        scope_items=[
            {"activity_code": "PNT-01", "activity_description": "Sand body"},
            {"activity_description": "Mask windows"},
        ])
    op_tt = TransactionType.query.filter_by(order_category="OPERATIONAL").first()
    op_order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        maintenance_type_id=None, transaction_type_id=op_tt.id,
        scheduled_date=date.today(), user=None)
    role = Role(name="sat-role")
    role.permissions = Permission.query.all()
    user = User(username="satuser", email="sat@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    view_role = Role(name="sat-view")
    view_role.permissions = Permission.query.filter(
        Permission.code.in_(["maintenanceorder.view"])).all()
    viewer = User(username="satviewer", email="satv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [view_role]
    db.session.add_all([role, user, view_role, viewer])
    db.session.commit()
    return {"order": order, "op_order": op_order, "mt": mt,
            "user": user, "viewer": viewer}


def _tok(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _hdr(client, username):
    return {"Authorization": f"Bearer {_tok(client, username)}"}


# ── service layer ────────────────────────────────────────────────────────

def test_save_as_template_creates_new_template_from_order_scope(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    order = env["order"]
    tmpl = MaintenanceOrderService().save_scope_as_template(
        order.id, name="My Repaint Checklist")
    assert tmpl is not None
    assert tmpl.id is not None
    assert tmpl.maintenance_type_id == env["mt"].id
    descs = [i.activity_description for i in tmpl.items]
    assert "Sand body" in descs
    assert "Mask windows" in descs


def test_save_as_template_preserves_optional_code(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    tmpl = MaintenanceOrderService().save_scope_as_template(
        env["order"].id, name="Code test")
    by_desc = {i.activity_description: i for i in tmpl.items}
    assert by_desc["Sand body"].activity_code == "PNT-01"
    assert by_desc["Mask windows"].activity_code is None


def test_save_as_template_rejects_operational_order(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderCategoryError)
    with pytest.raises(InvalidOrderCategoryError):
        MaintenanceOrderService().save_scope_as_template(
            env["op_order"].id, name="Op template")


def test_save_as_template_rejects_blank_name(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, ScopeValidationError)
    with pytest.raises(ScopeValidationError, match="name"):
        MaintenanceOrderService().save_scope_as_template(
            env["order"].id, name="   ")


def test_save_as_template_rejects_order_with_no_scope_items(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    from app.modules.master_data.reference.service import MaintenanceTypeService
    mt2 = MaintenanceTypeService().create(code="SAT-PM", name="PMS",
                                          category="PM")
    empty = MaintenanceOrderService().create(
        vehicle_id=env["order"].vehicle_id, maintenance_type_id=mt2.id,
        scheduled_date=date.today(), user=None)
    with pytest.raises(InvalidOrderStateError, match="no activities"):
        MaintenanceOrderService().save_scope_as_template(
            empty.id, name="Empty")


def test_new_template_is_visible_in_scope_template_list(db, env):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.modules.maintenance_config.service import PMScopeTemplateService
    count_before = len(PMScopeTemplateService().list())
    MaintenanceOrderService().save_scope_as_template(
        env["order"].id, name="Listed")
    assert len(PMScopeTemplateService().list()) == count_before + 1


# ── API layer ────────────────────────────────────────────────────────────

def test_api_save_as_template_returns_201_with_template_json(client, db, env):
    r = client.post(
        f"/api/v1/maintenance-orders/{env['order'].id}/save-as-template",
        headers=_hdr(client, "satuser"),
        json={"name": "API Template"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["name"] == "API Template"
    assert len(body.get("items", [])) == 2


def test_api_save_as_template_requires_pmscopetemplate_create(client, db, env):
    r = client.post(
        f"/api/v1/maintenance-orders/{env['order'].id}/save-as-template",
        headers=_hdr(client, "satviewer"),
        json={"name": "Should fail"})
    assert r.status_code == 403


def test_api_save_as_template_400_for_operational(client, db, env):
    r = client.post(
        f"/api/v1/maintenance-orders/{env['op_order'].id}/save-as-template",
        headers=_hdr(client, "satuser"),
        json={"name": "Operational"})
    assert r.status_code == 400


def test_api_save_as_template_400_for_blank_name(client, db, env):
    r = client.post(
        f"/api/v1/maintenance-orders/{env['order'].id}/save-as-template",
        headers=_hdr(client, "satuser"),
        json={"name": ""})
    assert r.status_code == 400
    assert "name" in r.get_data(as_text=True)


# ── PMScopeItem.activity_code nullable ──────────────────────────────────

def test_pm_scope_item_activity_code_is_nullable(db, env):
    """activity_code must become nullable in pm_scope_items (the master
    table) so save_scope_as_template can store a code=None line."""
    from app.modules.maintenance_config.models import PMScopeItem, PMScopeTemplate
    tmpl = PMScopeTemplate(maintenance_type_id=env["mt"].id, name="nullable test")
    tmpl.items = [PMScopeItem(activity_code=None,
                              activity_description="No code",
                              sort_order=1)]
    db.session.add(tmpl)
    db.session.commit()
    db.session.expire(tmpl)
    fresh = db.session.get(PMScopeTemplate, tmpl.id)
    assert fresh.items[0].activity_code is None
