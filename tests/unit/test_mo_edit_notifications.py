"""Document edit policy, Phase 1: approvers are told about edits made
after submission, and can see exactly what changed.

Spec: docs/superpowers/specs/2026-10-12-document-edit-policy-design.md

Michael chose NOT to restart approval when the initiator edits a
Pending or In-Progress order. What keeps an approver from approving
something they never saw is:

* an `edited_after_submission` event through the Notification Engine
  (configurable rules; seeded to the current approver and to everyone
  who already approved), and
* `changes_since_submission` on the order -- field, was, now, who,
  when -- read from the existing Audit Trail.
"""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.cli import _seed_notification_rules, _seed_transaction_types
from app.core.approval.models import ApprovalAction, ApprovalInstance
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.approval_config.models import ApprovalLevel, ApprovalPath
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.system_admin.models import InAppNotification, NotificationRule
from app.modules.system_admin.services.notification_engine import (
    NotificationEngine)
from app.modules.transactions.maintenance_order.models import (
    MaintenanceOrder, TransactionType)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User

EVENT = "edited_after_submission"


def _user(db, username, role):
    u = User(username=username, email=f"{username}@e.com",
             first_name=username.split("_")[-1].title(), last_name="Test",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add(u)
    db.session.commit()
    return u


@pytest.fixture()
def env(db):
    sync_permissions()
    _seed_transaction_types()
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="Maintenance Order",
                                     requires_approval=True,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code="MO").first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    view = Permission.query.filter_by(code="maintenanceorder.view").first()
    r_init = Role(name="EN Requester", permissions=[view])
    r_l1 = Role(name="EN Supervisor", permissions=[view])
    r_l2 = Role(name="EN Manager", permissions=[view])
    db.session.add_all([r_init, r_l1, r_l2])
    db.session.commit()
    initiator = _user(db, "en_initiator", r_init)
    sup = _user(db, "en_supervisor", r_l1)
    mgr = _user(db, "en_manager", r_l2)

    path = ApprovalPath(name="EN Two-Step")
    db.session.add(path)
    db.session.flush()
    db.session.add_all([
        ApprovalLevel(path_id=path.id, level_number=1,
                      approver_type="ROLE", role_id=r_l1.id),
        ApprovalLevel(path_id=path.id, level_number=2,
                      approver_type="ROLE", role_id=r_l2.id)])
    db.session.commit()

    manila = BranchService().create(code="BR-EN1", name="Manila Hub")
    munti = BranchService().create(code="BR-EN2", name="Muntinlupa")
    cebu = BranchService().create(code="BR-EN3", name="Cebu Branch")
    vt = VehicleTypeService().create(code="LV-EN", name="Light",
                                     category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2012,
        branch_id=manila.id, conduction_number="TIL-120")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, order_category="OPERATIONAL",
        transaction_type_id=TransactionType.query.filter_by(
            code="DEP-TRANSFER").first().id,
        destination_branch_id=munti.id, scheduled_date=date.today(),
        user=initiator)
    _seed_notification_rules()
    db.session.commit()
    return dict(order=order, initiator=initiator, sup=sup, mgr=mgr,
                path=path, munti=munti, cebu=cebu)


def _submit(db, env, status="PENDING", level=1, approved_by=(),
            order_status=None, submitted_at=None):
    """Put the order in an approval state, with the action history the
    engine would have written."""
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(
        document_type_id=dt.id, approval_path_id=env["path"].id,
        reference_table="maintenance_orders", reference_id=order.id,
        status=status, current_level=level,
        submitted_by=env["initiator"].id)
    db.session.add(inst)
    db.session.flush()
    at = submitted_at or (datetime.now(timezone.utc) - timedelta(minutes=5))
    db.session.add(ApprovalAction(instance_id=inst.id, level_number=1,
                                  action="SUBMIT",
                                  acted_by=env["initiator"].id, acted_at=at))
    for lvl, user in approved_by:
        db.session.add(ApprovalAction(instance_id=inst.id, level_number=lvl,
                                      action="APPROVE", acted_by=user.id,
                                      acted_at=at))
    order.approval_instance_id = inst.id
    if order_status:
        order.status = order_status
    db.session.commit()
    InAppNotification.query.delete()
    db.session.commit()
    return inst


def _edit(env, **fields):
    return MaintenanceOrderService().update(
        env["order"].id, user=env["initiator"], **fields)


def _notified(user):
    return InAppNotification.query.filter_by(
        user_id=user.id, event_code=EVENT).count()


# ── who is told ─────────────────────────────────────────────────────

def test_pending_edit_notifies_the_current_approver(db, env):
    _submit(db, env)
    _edit(env, destination_branch_id=env["cebu"].id)
    assert _notified(env["sup"]) == 1
    assert _notified(env["mgr"]) == 0      # level 2 has not been reached


def test_the_initiator_is_not_notified_of_their_own_edit(db, env):
    _submit(db, env)
    _edit(env, description="corrected")
    assert _notified(env["initiator"]) == 0


def test_in_progress_edit_notifies_everyone_who_approved_once_each(db, env):
    """The Manager is both the last-level CURRENT_APPROVER and a PRIOR
    approver -- two rules match, one message arrives."""
    _submit(db, env, status="APPROVED", level=2,
            approved_by=[(1, env["sup"]), (2, env["mgr"])],
            order_status="IN_PROGRESS")
    _edit(env, description="found a seized caliper")
    assert _notified(env["sup"]) == 1
    assert _notified(env["mgr"]) == 1


def test_unsubmitted_draft_edit_notifies_nobody(db, env):
    _edit(env, description="still drafting")
    assert InAppNotification.query.filter_by(event_code=EVENT).count() == 0


def test_returned_edit_notifies_nobody(db, env):
    """The approver asked for the fix; they see it on resubmit."""
    _submit(db, env, status="RETURNED")
    _edit(env, description="fixed as asked")
    assert InAppNotification.query.filter_by(event_code=EVENT).count() == 0


def test_a_save_that_changes_nothing_notifies_nobody(db, env):
    _submit(db, env)
    _edit(env, destination_branch_id=env["munti"].id)   # same as before
    assert InAppNotification.query.filter_by(event_code=EVENT).count() == 0


def test_seed_configures_the_edit_rules(db, env):
    kinds = {r.recipient_type for r in
             NotificationRule.query.filter_by(event_code=EVENT)}
    assert kinds == {"CURRENT_APPROVER", "PRIOR_APPROVERS"}


def test_prior_approvers_recipient_resolves_approve_actions(db, env):
    inst = _submit(db, env, status="APPROVED", level=2,
                   approved_by=[(1, env["sup"]), (2, env["mgr"])])
    rule = NotificationRule(event_code="x", recipient_type="PRIOR_APPROVERS")
    got = NotificationEngine()._resolve_recipients(rule, inst)
    assert {u.id for u in got} == {env["sup"].id, env["mgr"].id}


# ── what changed ────────────────────────────────────────────────────

def test_feed_lists_a_change_made_while_pending_with_names(db, env):
    _submit(db, env)
    _edit(env, destination_branch_id=env["cebu"].id)
    feed = MaintenanceOrderService().changes_since_submission(env["order"])
    assert len(feed) == 1
    c = feed[0]
    assert c["field"] == "destination_branch_id"
    assert c["label"] == "Destination branch"
    assert (c["old"], c["new"]) == ("Muntinlupa", "Cebu Branch")
    assert c["changed_at"]


def test_feed_ignores_edits_made_before_submission(db, env):
    _edit(env, description="before submit")
    _submit(db, env, submitted_at=datetime.now(timezone.utc)
            + timedelta(seconds=1))
    assert MaintenanceOrderService().changes_since_submission(
        env["order"]) == []


def test_feed_is_empty_for_an_unsubmitted_order(db, env):
    _edit(env, description="drafting")
    assert MaintenanceOrderService().changes_since_submission(
        env["order"]) == []


def test_feed_ignores_system_fields(db, env):
    """Status moves and the origin re-snapshot are not edits."""
    _submit(db, env, status="APPROVED")
    order = env["order"]
    order.status = "IN_PROGRESS"
    db.session.commit()
    assert MaintenanceOrderService().changes_since_submission(order) == []


def test_feed_formats_money(db, env):
    _submit(db, env)
    _edit(env, estimated_cost=15000)
    c = MaintenanceOrderService().changes_since_submission(env["order"])[0]
    assert c["new"] == "15,000.00"


def _hdr(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return {"Authorization":
            f"Bearer {json.loads(r.get_data(as_text=True))['access_token']}"}


def test_detail_carries_the_feed_with_who_changed_it(client, db, env):
    _submit(db, env)
    oid = env["order"].id
    r = client.patch(f"/api/v1/maintenance-orders/{oid}",
                     json={"destination_branch_id": env["cebu"].id},
                     headers=_hdr(client, "en_initiator"))
    assert r.status_code == 200, r.get_data(as_text=True)
    data = client.get(f"/api/v1/maintenance-orders/{oid}",
                      headers=_hdr(client, "en_supervisor")).get_json()
    feed = data["changes_since_submission"]
    assert feed[0]["new"] == "Cebu Branch"
    assert feed[0]["changed_by"] == "Initiator Test"


def test_feed_ignores_fields_that_were_resaved_unchanged(db, env):
    """The edit form re-posts every field. A value saved again as
    itself (15000 vs 15000.00, the same branch id) is not a change."""
    _submit(db, env)
    order = env["order"]
    _edit(env, estimated_cost=0, destination_branch_id=env["munti"].id,
          description="only this changed")
    feed = MaintenanceOrderService().changes_since_submission(order)
    assert [c["field"] for c in feed] == ["description"]
