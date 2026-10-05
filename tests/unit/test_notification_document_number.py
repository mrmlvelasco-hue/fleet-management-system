"""Notification engine: in-app and email use the real document number.

Before this fix, _send_in_app() produced messages like
  "MO #51 - returned"
using the raw reference_id. get_document_number() already existed for
emails; this extends the same resolution to in-app messages and the
email subject line.

Also adds the `description` key to the in-app and email context so
the seeded / custom templates can show a one-line summary of the
document (what kind of work, vehicle, etc.) without a magic link that
may not be clicked before the notification is dismissed.

And: `get_description()` in reference_resolver -- duck-typed like
get_scope_items() so adding it to a new module needs no change here.
"""
import json
from datetime import date

import pytest

from app.cli import _seed_transaction_types
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.system_admin.models import InAppNotification
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


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
    branch = BranchService().create(code="BR-ND", name="Notif Branch")
    vt = VehicleTypeService().create(code="LV-ND", name="Light",
                                      category="LIGHT")
    mt = MaintenanceTypeService().create(code="ND-CM", name="Corrective",
                                          category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf", year=2023,
        branch_id=branch.id, conduction_number="ND-001")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    role = Role(name="NR-role")
    role.permissions = Permission.query.all()
    user = User(username="notifuser", email="nu@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])
    db.session.commit()
    return {"order": order, "user": user, "vehicle": vehicle, "mt": mt}


# ── notification engine: in-app message ─────────────────────────────────

def test_in_app_message_shows_real_document_number_not_reference_id(db, env):
    """'MO #51' used to land in the message box -- meaningless to the
    recipient who sees it as a raw database id. The real document
    number ('MO-2026-000051') was already computed for emails; it was
    just never reached for in-app."""
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngineService)
    from app.core.approval.models import ApprovalInstance
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(
        document_type_id=dt.id,
        reference_table="maintenance_orders",
        reference_id=order.id,
        status="RETURNED")
    db.session.add(inst)
    db.session.commit()
    NotificationEngineService()._send_in_app(env["user"], "returned", inst)
    notif = (InAppNotification.query
             .filter_by(user_id=env["user"].id)
             .order_by(InAppNotification.id.desc())
             .first())
    assert notif is not None
    assert f"#{order.id}" not in notif.message, (
        f"Raw id leaked into message: {notif.message!r}")
    assert order.document_number in notif.message, (
        f"Document number missing from message: {notif.message!r}")


def test_in_app_message_has_readable_event_label(db, env):
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngineService)
    from app.core.approval.models import ApprovalInstance
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=order.id, status="APPROVED")
    db.session.add(inst)
    db.session.commit()
    NotificationEngineService()._send_in_app(env["user"], "approved_final", inst)
    notif = (InAppNotification.query
             .filter_by(user_id=env["user"].id)
             .order_by(InAppNotification.id.desc()).first())
    assert "Approved" in notif.message or "approved" in notif.message.lower()


def test_in_app_title_is_human_readable(db, env):
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngineService)
    from app.core.approval.models import ApprovalInstance
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=order.id, status="RETURNED")
    db.session.add(inst); db.session.commit()
    NotificationEngineService()._send_in_app(env["user"], "returned", inst)
    notif = (InAppNotification.query.filter_by(user_id=env["user"].id)
             .order_by(InAppNotification.id.desc()).first())
    assert notif.title and notif.title != "Document Returned"


# ── reference_resolver: get_description ─────────────────────────────────

def test_get_description_returns_vehicle_and_type_for_maintenance_order(db, env):
    from app.core.reference_resolver import get_description
    order = env["order"]
    desc = get_description("maintenance_orders", order.id)
    assert desc  # not empty
    # Should mention the vehicle (plate / conduction) or the maintenance type
    assert ("ND-001" in desc or "Isuzu" in desc or "Corrective" in desc), desc


def test_get_description_returns_empty_string_for_unknown_table(db):
    from app.core.reference_resolver import get_description
    assert get_description("no_such_table", 99) == ""


def test_get_description_returns_empty_string_for_missing_record(db, env):
    from app.core.reference_resolver import get_description
    assert get_description("maintenance_orders", 999999) == ""


# ── notification context ─────────────────────────────────────────────────

def test_notification_context_carries_description_key(db, env):
    from app.modules.system_admin.tasks import _build_notification_context
    order = env["order"]
    user = env["user"]
    ctx = _build_notification_context(user, "submitted",
                                      "maintenance_orders", order.id)
    assert "description" in ctx, "description key missing from context"
    assert ctx["description"]   # not blank for a real MO


def test_notification_context_description_empty_for_unknown_table(db, env):
    from app.modules.system_admin.tasks import _build_notification_context
    user = env["user"]
    ctx = _build_notification_context(user, "submitted", "trip_tickets", 99999)
    assert ctx.get("description", "") == ""


# ── email subject / queue ────────────────────────────────────────────────

def test_email_subject_uses_document_number_not_reference_id(client, db, env):
    """The email outbox subject showed raw ids before this fix."""
    from app.modules.system_admin.services.notification_engine import (
        NotificationEngineService)
    from app.modules.system_admin.models import EmailOutbox
    from app.core.approval.models import ApprovalInstance
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=order.id, status="PENDING")
    db.session.add(inst); db.session.commit()
    NotificationEngineService()._queue_email(env["user"], "submitted", inst)
    outbox = (EmailOutbox.query.filter_by(to_user_id=env["user"].id)
              .order_by(EmailOutbox.id.desc()).first())
    assert outbox is not None
    assert f"#{order.id}" not in outbox.subject, (
        f"Raw id in subject: {outbox.subject!r}")
    assert order.document_number in outbox.subject, (
        f"Document number missing from subject: {outbox.subject!r}")


# ── backfill CLI ─────────────────────────────────────────────────────────

def test_backfill_notifications_replaces_raw_ids_with_document_numbers(
        client, db, env):
    """flask notifications backfill-document-numbers should update
    InAppNotification.message rows that still hold the old
    '<table> #<id>' format."""
    from app.extensions import db as _db
    order = env["order"]
    old_msg = f"MO #{order.id} - submitted"
    notif = InAppNotification(
        user_id=env["user"].id, title="Old",
        message=old_msg, event_code="submitted",
        reference_table="maintenance_orders",
        reference_id=order.id)
    _db.session.add(notif)
    _db.session.commit()
    runner = client.application.test_cli_runner()
    result = runner.invoke(args=["notifications",
                                 "backfill-document-numbers"])
    assert result.exit_code == 0, result.output
    _db.session.expire(notif)
    assert f"#{order.id}" not in notif.message
    assert order.document_number in notif.message


def test_backfill_is_idempotent(client, db, env):
    from app.extensions import db as _db
    order = env["order"]
    good_msg = f"MO {order.document_number} - submitted"
    notif = InAppNotification(
        user_id=env["user"].id, title="Already good",
        message=good_msg, event_code="submitted",
        reference_table="maintenance_orders",
        reference_id=order.id)
    _db.session.add(notif); _db.session.commit()
    runner = client.application.test_cli_runner()
    runner.invoke(args=["notifications", "backfill-document-numbers"])
    _db.session.expire(notif)
    assert notif.message == good_msg


# ── For Your Action: returned items carry document_number ─────────────────

def _api_user(db, codes, username):
    role = Role(name=f"role-{username}")
    role.permissions = Permission.query.filter(
        Permission.code.in_(codes)).all()
    user = User(username=username, email=f"{username}@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user]); db.session.commit()


def _tok(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def test_returned_item_carries_real_document_number(client, db, env):
    """ForYourAction was building returned rows with document_number=None,
    so the React panel fell back to 'maintenance_orders #51'."""
    from app.core.approval.models import ApprovalInstance
    from app.modules.document_config.models import DocumentType
    _api_user(db, ["vehicle.view", "maintenanceorder.view"], "retuser")
    db.session.flush()
    retuser = User.query.filter_by(username="retuser").first()
    order = env["order"]
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(
        document_type_id=dt.id,
        reference_table="maintenance_orders",
        reference_id=order.id,
        status="RETURNED",
        submitted_by=retuser.id)
    db.session.add(inst); db.session.commit()
    tok = _tok(client, "retuser")
    r = client.get("/api/v1/dashboard/awaiting-approval",
                   headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    returned = [x for x in body.get("items", []) if x.get("kind") == "returned"]
    assert returned, f"No returned items; all: {body.get('items')}"
    assert returned[0]["document_number"] == order.document_number, (
        f"Expected {order.document_number!r}, got "
        f"{returned[0]['document_number']!r}")


def test_backfill_leaves_already_updated_messages_unchanged(client, db, env):
    """Explicitly covers the skip-check: a row without a raw #<id> token
    is skipped, so its message count of tokens does not change."""
    from app.extensions import db as _db
    order = env["order"]
    # Already correct: document number appears, no raw #<id>
    good = f"{order.document_number} — Corrective · Isuzu Elf ND-001 [Submitted]"
    notif = InAppNotification(
        user_id=env["user"].id, title="T", message=good,
        reference_table="maintenance_orders", reference_id=order.id)
    # Also insert one that still has the raw id -- to prove the command
    # ran and touched something, so the unchanged assertion is meaningful.
    stale_msg = f"MO #{order.id} - submitted"
    stale = InAppNotification(
        user_id=env["user"].id, title="S", message=stale_msg,
        reference_table="maintenance_orders", reference_id=order.id)
    _db.session.add_all([notif, stale]); _db.session.commit()
    runner = client.application.test_cli_runner()
    result = runner.invoke(args=["notifications", "backfill-document-numbers"])
    assert result.exit_code == 0
    _db.session.expire(notif); _db.session.expire(stale)
    # The already-correct message is untouched
    assert notif.message == good
    # The stale message was updated
    assert f"#{order.id}" not in stale.message
    assert order.document_number in stale.message
