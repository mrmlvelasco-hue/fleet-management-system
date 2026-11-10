"""Tests for the shared display-status resolver.

Extracted from maintenance_orders.py's own `_display_status` (the only
module that had this right): a document's physical `status` column
stays "DRAFT" through both submission and approval in this codebase's
shared BaseTransactionService recipe (see base_service.py's `submit()`
and every module's own `*.status = "..."` closing-action assignment) --
so `status` alone cannot tell a never-submitted draft apart from one
sitting with an approver, or one fully approved but not yet closed out.
Every other module either showed the raw "DRAFT" for all three, or (the
mobile API's own Purchase Request copy, `_pr_display_status`) nearly
always fell back to "Draft" for the same reason. One resolver, reused
everywhere, fixes both.
"""
import pytest

from app.core.approval.display_status import resolve_display_status


class _Instance:
    def __init__(self, status):
        self.status = status


def test_unsubmitted_draft_reads_as_draft():
    assert resolve_display_status("DRAFT", None) == "Draft"


def test_pending_approval_reads_as_awaiting_approval():
    assert resolve_display_status(
        "DRAFT", _Instance("PENDING")) == "Awaiting Approval"


def test_returned_reads_as_returned_for_revision():
    assert resolve_display_status(
        "DRAFT", _Instance("RETURNED")) == "Returned for Revision"


def test_rejected_instance_reads_as_rejected():
    assert resolve_display_status(
        "DRAFT", _Instance("REJECTED")) == "Rejected"


def test_approved_but_not_yet_closed_out_reads_as_approved():
    """The exact gap that prompted this: fully approved, but the
    module's own closing action (completing a Vehicle Registration,
    releasing a Trip Ticket, mounting a Tire, ...) hasn't run yet."""
    assert resolve_display_status(
        "DRAFT", _Instance("APPROVED")) == "Approved"


@pytest.mark.parametrize("status,expected", [
    ("CANCELLED", "Cancelled"),
    ("COMPLETED", "Completed"),
    ("IN_PROGRESS", "In Progress"),   # Maintenance Order
    ("ACTIVE", "Active"),             # ATD
    ("RELEASED", "Released"),         # Trip Ticket
    ("ORDERED", "Ordered"),           # Purchase Request
    ("RECEIVED", "Received"),         # Purchase Request
    ("MOUNTED", "Mounted"),           # Tire / Battery Transaction
    ("IN_TRANSIT", "In Transit"),     # Vehicle Movement
    ("EXPIRED", "Expired"),           # ATD
])
def test_a_closed_out_physical_status_wins_outright(status, expected):
    """Once the module's own closing action has run, that status is
    shown as-is -- an approval instance left over in any state (even a
    stale PENDING) must never override a status that has already moved
    on."""
    assert resolve_display_status(status, _Instance("PENDING")) == expected
    assert resolve_display_status(status, None) == expected


def test_blank_status_falls_back_to_draft():
    assert resolve_display_status("", None) == "Draft"
    assert resolve_display_status(None, None) == "Draft"
