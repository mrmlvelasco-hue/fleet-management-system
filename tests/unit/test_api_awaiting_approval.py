"""Tests for the awaiting-approval worklist endpoint.

Backs the React "Awaiting Your Approval" panel. The rows must match what
the Jinja "For My Action" worklist shows, because the two are the same
queue: an approver seeing a document in one UI and not the other has no
way to tell which is authoritative.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def wl_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Approver Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    approver = User(username="approver", email="ap@example.com",
                    password_hash=hash_password("secret123"), is_active=True)
    approver.roles = [role]

    other = User(username="other", email="other@example.com",
                 password_hash=hash_password("secret123"), is_active=True)
    other.roles = [Role(name="Other Role")]

    db.session.add_all([role, approver, other])
    db.session.commit()
    return approver, other


def _token(client, username="approver"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(t):
    return {"Authorization": f"Bearer {t}"}


def _get(client, url, token):
    r = client.get(url, headers=_auth(token))
    return r.status_code, json.loads(r.get_data(as_text=True))


def _make_task(db, user, **over):
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO-WL").first()
    if dt is None:
        dt = DocumentType(code="MO-WL", name="Maintenance Order")
        db.session.add(dt)
        db.session.flush()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=over.get("reference_id", 1),
                            status="PENDING")
    db.session.add(inst)
    db.session.flush()
    kw = dict(approval_instance_id=inst.id, document_type_id=dt.id,
              level_number=2,
              document_number="MO-2026-000001",
              reference_table="maintenance_orders", reference_id=1,
              assigned_user_id=user.id, status="PENDING")
    kw.update(over)
    t = ApprovalTask(**kw)
    db.session.add(t)
    db.session.commit()
    return t


def test_requires_a_token(db, client, wl_env):
    assert client.get("/api/v1/dashboard/awaiting-approval").status_code == 401


def test_returns_the_documented_fields(db, client, wl_env):
    approver, _ = wl_env
    _make_task(db, approver)
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/awaiting-approval", token)
    assert status == 200
    assert body["total"] >= 1
    item = body["items"][0]
    assert set(item) >= {"task_id", "document_number", "document_type",
                         "plate_number", "type_label", "level_number",
                         "assigned_to", "requested_by", "created_at"}


def test_lists_only_tasks_for_the_signed_in_user(db, client, wl_env):
    """The queue is per-approver. Another user's pending task appearing
    here would invite someone to act on a decision that is not theirs."""
    approver, other = wl_env
    _make_task(db, other, document_number="MO-OTHER-0001")
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/awaiting-approval", token)
    numbers = [i["document_number"] for i in body["items"]]
    assert "MO-OTHER-0001" not in numbers


def test_suppresses_the_noisy_table_id_fallback(db, client, wl_env):
    """ApprovalTask.document_number is a DENORMALISED copy taken at
    submit time. When auto-numbering assigns the number later, that copy
    stays null forever -- which is why a real MO once rendered as
    "(no number)" on the dashboard while the MO list showed its number.

    The endpoint therefore re-resolves against the live record. Here
    there is no such record, so resolution legitimately fails, and what
    this asserts is the OTHER half: that the resolver's
    "<table> #<id>" fallback never reaches the UI as if it were a
    document number.

    NOTE: the successful-resolution path is not covered by this test --
    it needs a real MaintenanceOrder row. The Jinja worklist exercises
    the same helper, but that is not the same as covering it here.
    """
    approver, _ = wl_env
    _make_task(db, approver, document_number=None)
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/awaiting-approval", token)
    number = body["items"][0]["document_number"]
    assert number is None or not number.startswith("maintenance_orders")


def test_respects_the_limit_parameter(db, client, wl_env):
    approver, _ = wl_env
    for i in range(3):
        _make_task(db, approver, reference_id=i + 1,
                   document_number=f"MO-2026-00000{i}")
    token = _token(client)
    _, body = _get(client,
                   "/api/v1/dashboard/awaiting-approval?limit=2", token)
    assert len(body["items"]) <= 2
    # `total` is the full queue size, so the panel can page honestly
    # rather than implying the limit is the whole story.
    assert body["total"] >= 3


def test_empty_queue_is_not_an_error(db, client, wl_env):
    """Nothing waiting is the good case, and the panel says so."""
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/awaiting-approval", token)
    assert status == 200
    assert body["items"] == []
    assert body["total"] == 0
