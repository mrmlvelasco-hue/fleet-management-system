import pytest

from app.core.approval.engine import (
    ApprovalEngine, NotEligibleApproverError, InvalidStateError)
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import DocumentTypeService
from app.modules.user_management.models import Role, User


@pytest.fixture()
def env(db):
    """Doc type PR w/ 2-level path: Supervisor then Fleet Manager (roles)."""
    sup, mgr = Role(name="Supervisor"), Role(name="Fleet Manager")
    alice = User(username="alice", email="a@x.com", password_hash="x")
    alice.roles.append(sup)
    bob = User(username="bob", email="b@x.com", password_hash="x")
    bob.roles.append(mgr)
    carol = User(username="carol", email="c@x.com", password_hash="x")
    db.session.add_all([sup, mgr, alice, bob, carol])
    db.session.commit()

    dt = DocumentTypeService().create(code="PR", name="Purchase Request",
                                      requires_approval=True)
    path = ApprovalPathService().create(name="Two-Step", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": sup.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": mgr.id}])
    ApprovalMatrixService().create(dt.id, path.id)
    return dt, alice, bob, carol


def test_full_approval_walk(db, env):
    dt, alice, bob, carol = env
    eng = ApprovalEngine()
    inst = eng.submit("PR", "purchase_requests", 1, user=carol)
    assert inst.status == "PENDING" and inst.current_level == 1
    eng.approve(inst, user=alice, remarks="ok level 1")
    assert inst.status == "PENDING" and inst.current_level == 2
    eng.approve(inst, user=bob, remarks="ok level 2")
    assert inst.status == "APPROVED"
    assert [a.action for a in inst.actions] == ["SUBMIT", "APPROVE", "APPROVE"]


def test_wrong_user_cannot_approve(db, env):
    dt, alice, bob, carol = env
    eng = ApprovalEngine()
    inst = eng.submit("PR", "purchase_requests", 2, user=carol)
    with pytest.raises(NotEligibleApproverError):
        eng.approve(inst, user=bob)  # bob is level-2, current level is 1
    with pytest.raises(NotEligibleApproverError):
        eng.approve(inst, user=carol)  # no role at all


def test_reject_is_terminal(db, env):
    dt, alice, bob, carol = env
    eng = ApprovalEngine()
    inst = eng.submit("PR", "purchase_requests", 3, user=carol)
    eng.reject(inst, user=alice, remarks="no budget")
    assert inst.status == "REJECTED"
    with pytest.raises(InvalidStateError):
        eng.approve(inst, user=alice)


def test_return_and_resubmit(db, env):
    dt, alice, bob, carol = env
    eng = ApprovalEngine()
    inst = eng.submit("PR", "purchase_requests", 4, user=carol)
    eng.return_document(inst, user=alice, remarks="fix qty")
    assert inst.status == "RETURNED"
    eng.resubmit(inst, user=carol)
    assert inst.status == "PENDING" and inst.current_level == 1


def test_cancel_rules(db, env):
    dt, alice, bob, carol = env
    eng = ApprovalEngine()
    inst = eng.submit("PR", "purchase_requests", 5, user=carol)
    eng.cancel(inst, user=carol)
    assert inst.status == "CANCELLED"
    inst2 = eng.submit("PR", "purchase_requests", 6, user=carol)
    eng.approve(inst2, user=alice)
    eng.approve(inst2, user=bob)
    with pytest.raises(InvalidStateError):
        eng.cancel(inst2, user=carol)  # already APPROVED


def test_user_level_eligibility(db, env):
    dt, alice, bob, carol = env
    path = ApprovalPathService().create(name="Named", levels=[
        {"level_number": 1, "approver_type": "USER", "user_id": carol.id}])
    dt2 = DocumentTypeService().create(code="ATD", name="Authority To Drive",
                                       requires_approval=True)
    ApprovalMatrixService().create(dt2.id, path.id)
    eng = ApprovalEngine()
    inst = eng.submit("ATD", "atd", 1, user=alice)
    with pytest.raises(NotEligibleApproverError):
        eng.approve(inst, user=alice)
    eng.approve(inst, user=carol)
    assert inst.status == "APPROVED"


def test_requires_approval_false_short_circuits(db, env):
    DocumentTypeService().create(code="TT", name="Trip Ticket",
                                 requires_approval=False)
    eng = ApprovalEngine()
    inst = eng.submit("TT", "trip_tickets", 1, user=env[3])
    assert inst.status == "APPROVED"


def test_event_hook_fires(db, env):
    dt, alice, bob, carol = env
    events = []
    eng = ApprovalEngine()
    eng.on_event(lambda name, instance: events.append(name))
    inst = eng.submit("PR", "purchase_requests", 7, user=carol)
    eng.approve(inst, user=alice)
    assert "submitted" in events and "approved_level" in events
