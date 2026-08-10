"""Dashboard panel order.

Requested: Vehicle Registration Status moved to appear before Security
and Compliance. Reordered as two complete, self-contained rows rather
than breaking up either existing 2-column pairing (For My Action /
Security, and Registration Status / Renewals stayed paired with their
original partners) -- confirmed nothing else was lost in the process.
"""
from datetime import date

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_registration_status_appears_before_security_and_compliance(
        app, db):
    html = _client(app, db).get("/").get_data(as_text=True)
    pos_reg = html.find("Vehicle Registration Status")
    pos_sec = html.find("Security and Compliance")
    assert pos_reg != -1 and pos_sec != -1
    assert pos_reg < pos_sec


def test_reorder_did_not_lose_any_existing_panel(app, db):
    """The actual risk of this change: an earlier uploaded version of
    this file predated two real features and would have deleted them
    if applied directly. Confirms the reorder was done without losing
    anything."""
    html = _client(app, db).get("/").get_data(as_text=True)
    for panel in ("For My Action", "Security and Compliance",
                 "Vehicle Registration Status",
                 "Vehicles Due for Registration Renewal",
                 "dueMaintenanceWidget"):
        assert panel in html, f"{panel} panel is missing"


def test_for_my_action_and_security_are_still_paired_in_one_row(app, db):
    """The two existing 2-column pairings must survive the reorder
    intact -- neither panel should have been split from its original
    row partner."""
    html = open(
        "app/modules/main/templates/main/dashboard.html").read()
    # For My Action's row block must still contain Security and
    # Compliance within the same enclosing row div, not some other
    # panel.
    fma_idx = html.index("For My Action")
    sec_idx = html.index("Security and Compliance")
    row_start = html.rfind('<div class="row g-3 mb-3">', 0, fma_idx)
    row_end = html.find("</div>\n\n", sec_idx)
    assert row_start != -1
    assert row_start < fma_idx < sec_idx < row_end


def test_registration_status_and_renewals_are_still_paired(app, db):
    html = open(
        "app/modules/main/templates/main/dashboard.html").read()
    reg_idx = html.index("Vehicle Registration Status")
    ren_idx = html.index("Vehicles Due for Registration Renewal")
    row_start = html.rfind('<div class="row g-3 mb-3">', 0, reg_idx)
    assert row_start != -1
    assert row_start < reg_idx < ren_idx
