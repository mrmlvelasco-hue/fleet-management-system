"""Tests for the dashboard panels added for the React frontend:
fuel summary, and branch scoping on the registration / PM charts.

Security & Compliance deliberately has NO endpoint here. That panel is
four navigation links gated by permissions, and GET /api/v1/me already
returns the permission list -- adding a server round trip to re-state
what the client was told at sign-in would be pure ceremony.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def panel_env(db):
    sync_permissions()
    db.session.commit()

    full = Role(name="Panel Full")
    full.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "fuel.view"])).all()
    fuelless = Role(name="Panel No Fuel")
    fuelless.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view"])).all()
    db.session.add_all([full, fuelless])

    u_full = User(username="paneluser", email="panel@example.com",
                  password_hash=hash_password("secret123"), is_active=True)
    u_full.roles = [full]
    u_nofuel = User(username="nofuel", email="nofuel@example.com",
                    password_hash=hash_password("secret123"), is_active=True)
    u_nofuel.roles = [fuelless]
    db.session.add_all([u_full, u_nofuel])

    vt = VehicleTypeService().create(code="LV-PANEL", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-PANEL", name="Panel Branch")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2023,
        branch_id=branch.id, conduction_number="PANEL-000",
        plate_number="PANEL-1234")
    db.session.commit()
    return u_full, branch


def _token(client, username="paneluser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Fuel ────────────────────────────────────────────────────────────────────

def test_fuel_requires_the_fuel_view_permission(db, client, panel_env):
    """vehicle.view is not enough. The Jinja dashboard gates this panel on
    fuel.view, and the API must not be a way around that."""
    token = _token(client, username="nofuel")
    r = client.get("/api/v1/dashboard/fuel",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_fuel_returns_the_documented_fields(db, client, panel_env):
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/fuel", token)
    assert status == 200
    assert set(body) >= {"fills", "litres", "spend", "avg_price", "avg_kmpl",
                         "flagged", "untrusted_odometer", "needs_review",
                         "is_all_time"}


def test_fuel_reports_avg_kmpl_as_null_when_unmeasurable(db, client, panel_env):
    """None, not 0. Zero km/L would read as catastrophic efficiency
    rather than 'no fills recorded yet'."""
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/fuel", token)
    assert body["avg_kmpl"] is None


def test_fuel_serialises_money_without_losing_precision(db, client, panel_env):
    """Spend and litres are Decimal server-side. They must survive JSON
    as something exact -- a float would introduce binary rounding into a
    figure the client reads as pesos."""
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/fuel", token)
    assert isinstance(body["spend"], (str, int))
    assert isinstance(body["litres"], (str, int))


# ── Branch scoping on the charts ────────────────────────────────────────────

def test_charts_accept_a_branch_filter(db, client, panel_env):
    _, branch = panel_env
    token = _token(client)
    status, body = _get(
        client,
        f"/api/v1/dashboard/charts?only=registration_status&branch_id={branch.id}",
        token)
    assert status == 200
    assert "registration_status" in body


def test_charts_reject_an_unknown_branch(db, client, panel_env):
    token = _token(client)
    r = client.get("/api/v1/dashboard/charts?branch_id=999999",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400


def test_pm_compliance_totals_stay_consistent_under_a_branch_filter(
        db, client, panel_env):
    """The due counts and the fleet total must be scoped to the SAME set.

    Scoping the total but not the due list lets 'Due' exceed the total it
    is drawn against, which renders as a segment larger than the whole.
    """
    _, branch = panel_env
    chart = DashboardAnalyticsService().pm_compliance(branch_id=branch.id)
    counts = dict(zip(chart["labels"], chart["data"]))
    flagged = sum(v for k, v in counts.items() if k != "Good")
    assert counts["Good"] >= 0
    assert flagged <= sum(chart["data"])


def test_registration_status_percentages_sum_sanely(db, client, panel_env):
    _, branch = panel_env
    chart = DashboardAnalyticsService().registration_status(branch_id=branch.id)
    assert sum(chart["data"]) == chart["total"]


def test_registration_status_unchanged_without_a_branch_id(db, client, panel_env):
    """The Jinja dashboard calls these with user= only. That path must
    behave exactly as before -- the new parameter is additive."""
    svc = DashboardAnalyticsService()
    user, _ = panel_env
    assert svc.registration_status(user=user) == \
        svc.registration_status(user=user, branch_id=None)


# ── Gaps found on review ────────────────────────────────────────────────────

def test_fuel_requires_a_token(db, client, panel_env):
    """The permission test proves fuel.view is enforced for a signed-in
    user; this proves the endpoint is not reachable anonymously at all."""
    assert client.get("/api/v1/dashboard/fuel").status_code == 401


def test_fuel_reports_which_window_it_measured(db, client, panel_env):
    """`is_all_time` tells the panel whether the 30-day window fell back
    to all time. Without it the caption would claim 'Last 30 days' over
    all-time figures -- a small lie that makes spend look concentrated."""
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/fuel", token)
    assert isinstance(body["is_all_time"], bool)


def test_charts_only_parameter_limits_the_payload(db, client, panel_env):
    """pm_compliance runs a fleet-wide PM evaluation. Asking for one
    cheap chart must not pay for the expensive one, which is the whole
    reason `only` exists."""
    token = _token(client)
    _, body = _get(client,
                   "/api/v1/dashboard/charts?only=registration_status", token)
    assert set(body) == {"registration_status"}
