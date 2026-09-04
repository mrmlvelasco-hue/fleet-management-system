"""can_act_on -- the missing half of "may I approve this".

From a client screenshot: a trip ticket in DRAFT, carrying a
still-PENDING approval instance from an earlier submission, offered the
same person BOTH "Submit for Approval" and "Approve & Forward".

Eligibility alone was being asked. Whether the document was actually
awaiting a decision was not.
"""
import pytest

from app.modules.api.approval_eligibility import can_act_on


class _Inst:
    def __init__(self, status="PENDING"):
        self.status = status


class _User:
    id = 1


@pytest.fixture(autouse=True)
def eligible(monkeypatch):
    """Engine says yes to everything, so these tests isolate the NEW
    conditions rather than re-testing the engine."""
    from app.core.approval.engine import ApprovalEngine
    monkeypatch.setattr(ApprovalEngine, "is_eligible_approver",
                        lambda self, inst, user: True)


def test_true_for_a_pending_document_and_an_eligible_approver(app):
    assert can_act_on(_Inst(), _User(), "PENDING") is True


def test_TRUE_for_a_DRAFT_document_with_a_pending_instance(app):
    """Deliberate, and the opposite of my first attempt.

    Several modules -- trip tickets among them -- leave the document in
    DRAFT on submit and move only the approval instance to PENDING. So
    DRAFT + PENDING is the normal awaiting-approval state. Excluding it
    hid the decision from every legitimate approver; the
    return-and-resubmit test caught that within minutes."""
    assert can_act_on(_Inst("PENDING"), _User(), "DRAFT") is True


@pytest.mark.parametrize("status",
                         ["CANCELLED", "REJECTED", "CLOSED", "COMPLETED"])
def test_false_for_terminal_document_states(app, status):
    assert can_act_on(_Inst("PENDING"), _User(), status) is False


def test_status_matching_is_case_insensitive(app):
    """Modules spell status differently in places; a lowercase
    'cancelled' must not slip past the guard."""
    assert can_act_on(_Inst("PENDING"), _User(), "cancelled") is False


def test_false_when_the_instance_is_no_longer_pending(app):
    assert can_act_on(_Inst("APPROVED"), _User(), "PENDING") is False


def test_false_with_no_instance(app):
    assert can_act_on(None, _User(), "PENDING") is False


def test_false_for_an_anonymous_caller(app):
    assert can_act_on(_Inst(), None, "PENDING") is False


def test_status_is_optional_so_callers_without_one_still_work(app):
    assert can_act_on(_Inst(), _User()) is True
