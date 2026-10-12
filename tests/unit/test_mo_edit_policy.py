"""Document edit policy, Phase 1: the Maintenance Order.

Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md

The rule Michael confirmed:

* Draft (never submitted, Pending, Returned, Approved-awaiting-start)
  and In Progress orders are editable in FULL. Approval is NOT
  restarted by an edit; approvers are notified instead and can see
  what changed (covered in test_mo_edit_notifications.py).
* Completed, Cancelled and Rejected orders are locked.
* The initiator (requested_by) may edit their own order even without
  maintenanceorder.update; anyone holding that permission may edit
  too; everyone else may not.

This replaces the earlier rule (Pending -> NONE, In Progress ->
REMARKS_ONLY), which left the initiator unable to fix a wrong vendor
or destination branch while the order waited for approval.
"""
import json
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.approval.models import ApprovalInstance
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, InvalidOrderStateError, OrderEditForbiddenError)
from app.modules.user_management.models import Permission, Role, User


def _user(db, username, codes):
    role = Role(name=f"role-{username}")
    role.permissions = Permission.query.filter(
        Permission.code.in_(codes)).all()
    user = User(username=username, email=f"{username}@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return user


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="Maintenance Order",
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code="MO").first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    branch = BranchService().create(code="BR-EP", name="Edit Policy Branch")
    vt = VehicleTypeService().create(code="LV-EP", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-EP", name="PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2012,
        branch_id=branch.id, conduction_number="EP-0")
    initiator = _user(db, "ep_initiator", ["maintenanceorder.view"])
    editor = _user(db, "ep_editor",
                   ["maintenanceorder.view", "maintenanceorder.update"])
    stranger = _user(db, "ep_stranger", ["maintenanceorder.view"])
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=initiator)
    return {"order": order, "initiator": initiator, "editor": editor,
            "stranger": stranger, "vehicle": vehicle, "branch": branch}


def _with_approval(db, order, status, order_status=None):
    dt = DocumentType.query.filter_by(code="MO").first()
    instance = ApprovalInstance(
        document_type_id=dt.id, reference_table="maintenance_orders",
        reference_id=order.id, status=status)
    db.session.add(instance)
    db.session.flush()
    order.approval_instance_id = instance.id
    if order_status:
        order.status = order_status
    db.session.commit()
    db.session.expire_all()
    return db.session.get(MaintenanceOrder, order.id)


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    tok = json.loads(r.get_data(as_text=True))["access_token"]
    return {"Authorization": f"Bearer {tok}"}


# ── scope by state ──────────────────────────────────────────────────

@pytest.mark.parametrize("approval, order_status", [
    (None, "DRAFT"),
    ("PENDING", "DRAFT"),
    ("RETURNED", "DRAFT"),
    ("APPROVED", "DRAFT"),          # approved, work not started yet
    ("APPROVED", "IN_PROGRESS"),
])
def test_every_open_state_is_fully_editable(db, env, approval, order_status):
    order = env["order"]
    if approval:
        order = _with_approval(db, order, approval, order_status)
    assert MaintenanceOrderService().editable_scope(order) == "FULL"


@pytest.mark.parametrize("approval, order_status", [
    (None, "COMPLETED"),
    (None, "CANCELLED"),
    ("REJECTED", "DRAFT"),
])
def test_closed_states_are_locked(db, env, approval, order_status):
    order = env["order"]
    if approval:
        order = _with_approval(db, order, approval, order_status)
    else:
        order.status = order_status
        db.session.commit()
    assert MaintenanceOrderService().editable_scope(order) == "NONE"


def test_pending_order_accepts_a_cost_change(db, env):
    """The whole point: a wrong figure is fixed while it waits."""
    order = _with_approval(db, env["order"], "PENDING")
    MaintenanceOrderService().update(order.id, user=env["initiator"],
                                     estimated_cost=12345)
    assert float(db.session.get(MaintenanceOrder, order.id)
                 .estimated_cost) == 12345


def test_in_progress_order_accepts_a_cost_change(db, env):
    order = _with_approval(db, env["order"], "APPROVED", "IN_PROGRESS")
    MaintenanceOrderService().update(order.id, user=env["editor"],
                                     estimated_cost=500)
    assert float(db.session.get(MaintenanceOrder, order.id)
                 .estimated_cost) == 500


# ── who may edit ────────────────────────────────────────────────────

def test_initiator_can_edit_without_update_permission(db, env):
    assert not env["initiator"].has_permission("maintenanceorder.update")
    assert MaintenanceOrderService().can_edit(env["order"], env["initiator"])


def test_update_permission_holder_can_edit_someone_elses_order(db, env):
    assert MaintenanceOrderService().can_edit(env["order"], env["editor"])


def test_other_user_without_permission_cannot_edit(db, env):
    assert not MaintenanceOrderService().can_edit(env["order"],
                                                  env["stranger"])


def test_nobody_can_edit_a_completed_order(db, env):
    env["order"].status = "COMPLETED"
    db.session.commit()
    svc = MaintenanceOrderService()
    assert not svc.can_edit(env["order"], env["initiator"])
    assert not svc.can_edit(env["order"], env["editor"])


def test_update_refuses_a_user_who_may_not_edit(db, env):
    with pytest.raises(OrderEditForbiddenError):
        MaintenanceOrderService().update(env["order"].id,
                                         user=env["stranger"],
                                         description="not mine")


def test_forbidden_is_distinct_from_a_state_error(db, env):
    """The API maps them to different statuses (403 vs 409)."""
    assert not issubclass(OrderEditForbiddenError, InvalidOrderStateError)


# ── API ─────────────────────────────────────────────────────────────

def test_api_lets_the_initiator_edit_without_update_permission(
        client, db, env):
    r = client.patch(f"/api/v1/maintenance-orders/{env['order'].id}",
                     json={"description": "fixed by initiator"},
                     headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_json()["description"] == "fixed by initiator"


def test_api_refuses_another_user_without_permission(client, db, env):
    r = client.patch(f"/api/v1/maintenance-orders/{env['order'].id}",
                     json={"description": "hijack"},
                     headers=_hdr(client, "ep_stranger"))
    assert r.status_code == 403
    assert db.session.get(MaintenanceOrder,
                          env["order"].id).description != "hijack"


def test_api_lets_initiator_edit_while_pending(client, db, env):
    order = _with_approval(db, env["order"], "PENDING")
    r = client.patch(f"/api/v1/maintenance-orders/{order.id}",
                     json={"description": "fixed while pending"},
                     headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)


def test_api_refuses_edit_on_completed_order(client, db, env):
    env["order"].status = "COMPLETED"
    db.session.commit()
    r = client.patch(f"/api/v1/maintenance-orders/{env['order'].id}",
                     json={"description": "too late"},
                     headers=_hdr(client, "ep_editor"))
    assert r.status_code == 409


def test_detail_reports_can_edit_for_the_viewer(client, db, env):
    oid = env["order"].id
    mine = client.get(f"/api/v1/maintenance-orders/{oid}",
                      headers=_hdr(client, "ep_initiator")).get_json()
    other = client.get(f"/api/v1/maintenance-orders/{oid}",
                       headers=_hdr(client, "ep_stranger")).get_json()
    assert mine["can_edit"] is True
    assert other["can_edit"] is False


# ── the order's other editable parts follow the same gate ───────────

def test_api_initiator_can_replace_scope_of_work(client, db, env):
    r = client.put(f"/api/v1/maintenance-orders/{env['order'].id}/scope",
                   json={"items": [{"activity_description": "Check brakes"}]},
                   headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)


def test_api_stranger_cannot_replace_scope_of_work(client, db, env):
    r = client.put(f"/api/v1/maintenance-orders/{env['order'].id}/scope",
                   json={"items": [{"activity_description": "x"}]},
                   headers=_hdr(client, "ep_stranger"))
    assert r.status_code == 403


def test_api_initiator_can_add_and_remove_a_part(client, db, env):
    oid = env["order"].id
    r = client.post(f"/api/v1/maintenance-orders/{oid}/parts",
                    json={"part_description": "Brake pad", "quantity": 2},
                    headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 201, r.get_data(as_text=True)
    pid = r.get_json()["id"]
    r = client.delete(f"/api/v1/maintenance-orders/{oid}/parts/{pid}",
                      headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)


def test_api_stranger_cannot_add_a_part(client, db, env):
    r = client.post(f"/api/v1/maintenance-orders/{env['order'].id}/parts",
                    json={"part_description": "Brake pad"},
                    headers=_hdr(client, "ep_stranger"))
    assert r.status_code == 403


def test_api_stranger_cannot_remove_a_part(client, db, env):
    part = MaintenanceOrderService().add_part(
        env["order"].id, part_description="Filter")
    r = client.delete(
        f"/api/v1/maintenance-orders/{env['order'].id}/parts/{part.id}",
        headers=_hdr(client, "ep_stranger"))
    assert r.status_code == 403


def test_api_cannot_remove_a_part_belonging_to_another_order(
        client, db, env):
    """The initiator gate is checked on the order in the URL, so the part
    must belong to that order -- otherwise editing rights on your own
    order would reach into someone else's."""
    other = MaintenanceOrderService().create(
        vehicle_id=env["vehicle"].id, order_category="OPERATIONAL",
        transaction_type_id=_op_type_id("ADMINISTRATIVE"),
        scheduled_date=date.today(), user=env["editor"])
    foreign = MaintenanceOrderService().add_part(
        other.id, part_description="Not yours")
    r = client.delete(
        f"/api/v1/maintenance-orders/{env['order'].id}/parts/{foreign.id}",
        headers=_hdr(client, "ep_initiator"))
    assert r.status_code == 404
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrderPart)
    assert db.session.get(MaintenanceOrderPart, foreign.id) is not None


def _op_type_id(group):
    from app.modules.transactions.maintenance_order.models import (
        TransactionType)
    return TransactionType.query.filter_by(
        order_category="OPERATIONAL", group=group).first().id
