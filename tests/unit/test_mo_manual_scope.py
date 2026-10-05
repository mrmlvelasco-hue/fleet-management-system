"""Maintenance Order -- manual "Scope of Work" lines on any order.

Before this, an order only ever got a structured activity list by
copying a PM Scope Template at creation, so a non-PMS job (Repainting
under a normal work order, an Operational/Administrative request) had
nowhere to put its activities except free-text remarks.

The per-order table already existed -- maintenance_checklist_items is a
copy-on-create snapshot, so it never writes back to the master
template. This feature makes that snapshot editable:

  * any category (MAINTENANCE and OPERATIONAL) may carry lines;
  * code optional, description required;
  * editable only while editable_scope() == "FULL" (Draft never
    submitted, or Returned) -- locked once it is with an approver;
  * a template still pre-fills; the order's edited lines win and the
    master template is never changed;
  * the lines reach the approval email context.
"""
import json
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.approval.models import ApprovalInstance
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.maintenance_config.models import PMScopeItem, PMScopeTemplate
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import (
    MaintenanceChecklistItem, MaintenanceOrder, TransactionType)
from app.modules.transactions.maintenance_order.service import (
    IncompleteChecklistError, InvalidOrderStateError, MaintenanceOrderService,
    ScopeValidationError)
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
    branch = BranchService().create(code="BR-MS", name="Manual Scope Branch")
    vt = VehicleTypeService().create(code="LV-MS", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="MS-CM", name="Corrective",
                                         category="CM")
    mt_pm = MaintenanceTypeService().create(code="MS-PM", name="PMS",
                                            category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2023,
        branch_id=branch.id, conduction_number="MS-001")
    tmpl = PMScopeTemplate(maintenance_type_id=mt.id, name="Body Repaint")
    tmpl.items = [
        PMScopeItem(activity_code="PNT-01", activity_description="Sand body",
                    sort_order=1),
        PMScopeItem(activity_code="PNT-02", activity_description="Prime",
                    sort_order=2),
    ]
    db.session.add(tmpl)
    tmpl2 = PMScopeTemplate(maintenance_type_id=mt.id, name="Underwash")
    tmpl2.items = [PMScopeItem(activity_code="UW-01",
                               activity_description="Underwash",
                               sort_order=1)]
    db.session.add(tmpl2)
    db.session.commit()
    op_tt = (TransactionType.query
             .filter_by(order_category="OPERATIONAL").first())
    return {"vehicle": vehicle, "mt": mt, "mt_pm": mt_pm, "tmpl": tmpl,
            "tmpl2": tmpl2, "op_tt": op_tt}


def _create(env, **kw):
    params = dict(vehicle_id=env["vehicle"].id, scheduled_date=date.today(),
                  user=None, maintenance_type_id=env["mt"].id)
    params.update(kw)
    return MaintenanceOrderService().create(**params)


def _lines(order):
    return [(i.activity_code, i.activity_description, i.origin)
            for i in sorted(order.checklist_items, key=lambda x: x.sort_order)]


def _attach_approval(db, order, status):
    instance = ApprovalInstance(
        document_type_id=1, reference_table="maintenance_orders",
        reference_id=order.id, status=status)
    db.session.add(instance)
    db.session.flush()
    order.approval_instance_id = instance.id
    db.session.commit()
    db.session.expire_all()
    return db.session.get(MaintenanceOrder, order.id)


# ── create ──────────────────────────────────────────────────────────────

def test_maintenance_order_accepts_manual_lines_without_template(db, env):
    order = _create(env, scope_items=[
        {"activity_code": "", "activity_description": "Repaint left door"},
        {"activity_code": "PNT-9", "activity_description": "Clear coat"},
    ])
    assert _lines(order) == [
        (None, "Repaint left door", "MANUAL"),
        ("PNT-9", "Clear coat", "MANUAL"),
    ]
    assert [i.sort_order for i in order.checklist_items] == [1, 2]


def test_operational_order_keeps_manual_lines(db, env):
    """The template is still nulled for OPERATIONAL, but the order's own
    lines are not a template and must survive."""
    order = _create(env, order_category="OPERATIONAL",
                    maintenance_type_id=None,
                    transaction_type_id=env["op_tt"].id,
                    scope_items=[{"activity_description": "Secure LTO form"}])
    assert order.scope_template_id is None
    assert _lines(order) == [(None, "Secure LTO form", "MANUAL")]


def test_edited_lines_win_over_template_and_master_is_untouched(db, env):
    order = _create(env, scope_template_id=env["tmpl"].id, scope_items=[
        {"activity_code": "PNT-01", "activity_description": "Sand body + hood"},
        {"activity_description": "Mask windows"},
    ])
    assert order.scope_template_id == env["tmpl"].id
    assert [d for _, d, _ in _lines(order)] == ["Sand body + hood",
                                               "Mask windows"]
    db.session.expire_all()
    master = db.session.get(PMScopeTemplate, env["tmpl"].id)
    assert [i.activity_description for i in master.items] == ["Sand body",
                                                              "Prime"]


def test_template_without_scope_items_still_copies(db, env):
    """Backward compatibility: callers that never send scope_items."""
    order = _create(env, scope_template_id=env["tmpl"].id)
    assert _lines(order) == [("PNT-01", "Sand body", "TEMPLATE"),
                             ("PNT-02", "Prime", "TEMPLATE")]


@pytest.mark.parametrize("rows,needle", [
    ([{"activity_code": "X", "activity_description": "   "}], "description"),
    ([{"activity_description": "x" * 256}], "255"),
    ([{"activity_code": "C" * 41, "activity_description": "ok"}], "40"),
])
def test_invalid_lines_are_rejected(db, env, rows, needle):
    with pytest.raises(ScopeValidationError) as exc:
        _create(env, scope_items=rows)
    assert needle in str(exc.value)
    assert MaintenanceOrder.query.count() == 0


# ── replace_scope ───────────────────────────────────────────────────────

def test_replace_scope_updates_adds_and_removes(db, env):
    order = _create(env, scope_template_id=env["tmpl"].id)
    keep, drop = sorted(order.checklist_items, key=lambda i: i.sort_order)
    MaintenanceOrderService().replace_scope(order.id, [
        {"id": keep.id, "activity_code": "PNT-01",
         "activity_description": "Sand whole body"},
        {"activity_description": "Repaint roof"},
    ])
    db.session.expire_all()
    fresh = db.session.get(MaintenanceOrder, order.id)
    assert _lines(fresh) == [("PNT-01", "Sand whole body", "TEMPLATE"),
                             (None, "Repaint roof", "MANUAL")]
    assert fresh.checklist_items[0].id == keep.id  # updated in place
    assert db.session.get(MaintenanceChecklistItem, drop.id) is None
    master = db.session.get(PMScopeTemplate, env["tmpl"].id)
    assert len(master.items) == 2


def test_replace_scope_allowed_when_returned(db, env):
    order = _attach_approval(db, _create(env), "RETURNED")
    MaintenanceOrderService().replace_scope(
        order.id, [{"activity_description": "Fix after return"}])
    assert len(order.checklist_items) == 1


@pytest.mark.parametrize("approval,status", [
    ("PENDING", "DRAFT"), ("APPROVED", "IN_PROGRESS"),
    ("APPROVED", "COMPLETED"),
])
def test_replace_scope_locked_after_submission(db, env, approval, status):
    order = _attach_approval(db, _create(env), approval)
    order.status = status
    db.session.commit()
    with pytest.raises(InvalidOrderStateError):
        MaintenanceOrderService().replace_scope(
            order.id, [{"activity_description": "late change"}])


def test_replace_scope_rejects_a_row_from_another_order(db, env):
    other = _create(env, scope_items=[{"activity_description": "A"}])
    other_row_id = other.checklist_items[0].id
    # second order needs a different maintenance type (open-order guard)
    order = _create(env, maintenance_type_id=env["mt_pm"].id)
    with pytest.raises(ScopeValidationError):
        MaintenanceOrderService().replace_scope(
            order.id, [{"id": other_row_id, "activity_description": "B"}])


# ── update(): template change re-seeds ──────────────────────────────────

def test_changing_template_on_draft_reseeds_lines(db, env):
    order = _create(env, scope_template_id=env["tmpl"].id)
    MaintenanceOrderService().update(order.id,
                                     scope_template_id=env["tmpl2"].id)
    db.session.expire_all()
    fresh = db.session.get(MaintenanceOrder, order.id)
    assert _lines(fresh) == [("UW-01", "Underwash", "TEMPLATE")]


def test_resaving_same_template_keeps_edited_lines(db, env):
    order = _create(env, scope_template_id=env["tmpl"].id,
                    scope_items=[{"activity_description": "custom"}])
    MaintenanceOrderService().update(order.id,
                                     scope_template_id=env["tmpl"].id)
    db.session.expire_all()
    fresh = db.session.get(MaintenanceOrder, order.id)
    assert [d for _, d, _ in _lines(fresh)] == ["custom"]


# ── completion gate ─────────────────────────────────────────────────────

def test_pm_completion_gate_counts_manual_lines(db, env):
    order = _create(env, maintenance_type_id=env["mt_pm"].id,
                    scope_items=[{"activity_description": "Extra check"}])
    order = _attach_approval(db, order, "APPROVED")
    order.status = "IN_PROGRESS"
    db.session.commit()
    with pytest.raises(IncompleteChecklistError):
        MaintenanceOrderService().complete(order.id, actual_cost=0,
                                           completed_date=date.today())


# ── API ─────────────────────────────────────────────────────────────────

def _api_user(db, codes, username):
    role = Role(name=f"role-{username}")
    role.permissions = Permission.query.filter(
        Permission.code.in_(codes)).all()
    user = User(username=username, email=f"{username}@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def editor(db, env):
    _api_user(db, ["maintenanceorder.view", "maintenanceorder.create",
                   "maintenanceorder.update"], "scopeeditor")
    return "scopeeditor"


def test_api_create_with_scope_items(client, db, env, editor):
    r = client.post("/api/v1/maintenance-orders", headers=_hdr(client, editor),
                    json={"vehicle_id": env["vehicle"].id,
                          "maintenance_type_id": env["mt"].id,
                          "scheduled_date": date.today().isoformat(),
                          "scope_items": [
                              {"activity_code": "",
                               "activity_description": "Repaint bumper"}]})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body["checklist_items"][0]["activity_code"] is None
    assert body["checklist_items"][0]["activity_description"] == "Repaint bumper"
    assert body["checklist_items"][0]["origin"] == "MANUAL"


def test_api_create_rejects_blank_description(client, db, env, editor):
    r = client.post("/api/v1/maintenance-orders", headers=_hdr(client, editor),
                    json={"vehicle_id": env["vehicle"].id,
                          "maintenance_type_id": env["mt"].id,
                          "scope_items": [{"activity_description": ""}]})
    assert r.status_code == 400
    assert "scope_items" in r.get_data(as_text=True)


def test_api_put_scope_replaces_lines(client, db, env, editor):
    order = _create(env)
    r = client.put(f"/api/v1/maintenance-orders/{order.id}/scope",
                   headers=_hdr(client, editor),
                   json={"items": [{"activity_description": "Line A"},
                                   {"activity_description": "Line B"}]})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert [i["activity_description"]
            for i in r.get_json()["checklist_items"]] == ["Line A", "Line B"]


def test_api_put_scope_conflict_when_pending(client, db, env, editor):
    order = _attach_approval(db, _create(env), "PENDING")
    r = client.put(f"/api/v1/maintenance-orders/{order.id}/scope",
                   headers=_hdr(client, editor),
                   json={"items": [{"activity_description": "x"}]})
    assert r.status_code == 409


def test_api_put_scope_requires_update_permission(client, db, env):
    _api_user(db, ["maintenanceorder.view"], "scopeviewer")
    order = _create(env)
    r = client.put(f"/api/v1/maintenance-orders/{order.id}/scope",
                   headers=_hdr(client, "scopeviewer"),
                   json={"items": []})
    assert r.status_code == 403


def test_scope_template_details_for_generic_template(client, db, env, editor):
    """Latent NameError: `vehicle` was only bound when the template had a
    linked PM schedule with a work description, so a generic template
    (no schedule) 500'd -- and the form's prefill now depends on it."""
    r = client.get(
        "/api/v1/maintenance-orders/scope-template-details"
        f"?template_id={env['tmpl'].id}&vehicle_id={env['vehicle'].id}",
        headers=_hdr(client, editor))
    assert r.status_code == 200
    assert [i["activity_description"] for i in r.get_json()["items"]] == [
        "Sand body", "Prime"]


# ── email ───────────────────────────────────────────────────────────────

def test_notification_context_carries_scope_items(db, env):
    from app.modules.system_admin.tasks import _build_notification_context
    order = _create(env, scope_items=[
        {"activity_code": "PNT-9", "activity_description": "Clear coat"},
        {"activity_description": "Buff"}])
    user = User(username="ctx", email="ctx@e.com", password_hash="x")
    ctx = _build_notification_context(user, "submitted",
                                      "maintenance_orders", order.id)
    assert ctx["scope_items"] == [
        {"code": "PNT-9", "description": "Clear coat"},
        {"code": "", "description": "Buff"}]


def test_notification_context_scope_items_empty_for_other_tables(db, env):
    from app.modules.system_admin.tasks import _build_notification_context
    user = User(username="ctx2", email="ctx2@e.com", password_hash="x")
    ctx = _build_notification_context(user, "submitted", "trip_tickets", 999)
    assert ctx["scope_items"] == []


def test_fallback_and_seeded_templates_render_scope(db, env):
    from jinja2 import Template
    from app.cli import _seed_email_templates
    from app.modules.system_admin.models import EmailTemplate
    from app.modules.system_admin.tasks import _FALLBACK_BODY_HTML
    ctx = {"recipient_name": "R", "reference_table": "maintenance_orders",
           "reference_id": 1, "event_label": "Submitted",
           "document_number": "MO-1", "view_url": "", "comment_body": "",
           "author_name": "",
           "scope_items": [{"code": "", "description": "Repaint door"}]}
    assert "Repaint door" in Template(_FALLBACK_BODY_HTML).render(**ctx)
    _seed_email_templates()
    t = EmailTemplate.query.filter_by(event_code="submitted").first()
    assert "Repaint door" in Template(t.body_html).render(**ctx)
    assert "Repaint door" in Template(t.body_text).render(**ctx)
    ctx["scope_items"] = []
    assert "Scope of Work" not in Template(t.body_html).render(**ctx)


def test_template_origin_hint_honoured_only_with_a_template(db, env):
    with_tmpl = _create(env, scope_template_id=env["tmpl"].id, scope_items=[
        {"activity_code": "PNT-01", "activity_description": "Sand body",
         "origin": "TEMPLATE"},
        {"activity_description": "Mask windows", "origin": "TEMPLATE_X"}])
    assert [o for _, _, o in _lines(with_tmpl)] == ["TEMPLATE", "MANUAL"]
    no_tmpl = _create(env, maintenance_type_id=env["mt_pm"].id, scope_items=[
        {"activity_description": "Claimed", "origin": "TEMPLATE"}])
    assert [o for _, _, o in _lines(no_tmpl)] == ["MANUAL"]


def test_scope_lines_are_html_escaped_in_email(db, env):
    """Descriptions are user-entered; the email renders with a plain
    (non-autoescaping) jinja2.Template, so the block must escape."""
    from jinja2 import Template
    from app.modules.system_admin.tasks import SCOPE_BLOCK_HTML
    html = Template(SCOPE_BLOCK_HTML).render(scope_items=[
        {"code": "<b>", "description": "<script>x</script>"}])
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<b>" not in html
