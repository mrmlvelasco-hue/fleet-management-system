"""Tests for the REST dashboard API consumed by the React frontend.

The whole point of this surface is that it must NOT be a second
implementation of the dashboard. Every number it returns has to come
from the same DashboardService / DashboardAnalyticsService the Jinja
dashboard already calls, or the two dashboards will quietly disagree --
which is worse than either being wrong on its own, because whoever spots
it has no way to know which one to believe.

These tests therefore assert equality against the services directly,
not against hardcoded expected values.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.dashboard_service import DashboardService
from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import User, Role, Permission
from app.core.security.registry import sync_permissions


@pytest.fixture()
def dash_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Dashboard Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["vehicle.view", "maintenanceorder.view"])).all()
    db.session.add(role)

    user = User(username="dashuser", email="dash@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)

    # A user with NO dashboard-relevant permissions, to prove the
    # endpoints are actually gated rather than merely authenticated.
    plain_role = Role(name="No Access Role")
    plain = User(username="nodash", email="nodash@example.com",
                 password_hash=hash_password("secret123"), is_active=True)
    plain.roles = [plain_role]
    db.session.add_all([plain_role, plain])

    vt = VehicleTypeService().create(code="LV-DASH", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-DASH", name="Dash Branch")
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="DASH-000",
        plate_number="DASH-1234")
    db.session.commit()
    return user, branch


def _token(client, username="dashuser", password="secret123"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": password})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _get(client, url, token):
    r = client.get(url, headers=_auth(token))
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Authentication & authorisation ──────────────────────────────────────────

def test_summary_requires_a_token(db, client, dash_env):
    assert client.get("/api/v1/dashboard/summary").status_code == 401


def test_summary_requires_vehicle_view_permission(db, client, dash_env):
    token = _token(client, username="nodash")
    assert client.get("/api/v1/dashboard/summary",
                      headers=_auth(token)).status_code == 403


# ── Summary KPIs ────────────────────────────────────────────────────────────

def test_summary_matches_the_dashboard_service_exactly(db, client, dash_env):
    """The React KPI numbers must be the Jinja KPI numbers."""
    user, _ = dash_env
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/summary", token)
    assert status == 200

    dash = DashboardService()
    assert body["fleet_count"] == dash.fleet_count(user=user)
    assert body["tire_stock_count"] == dash.tire_stock_count(user=user)
    assert body["battery_stock_count"] == dash.battery_stock_count(user=user)


def test_summary_includes_the_deferred_counts(db, client, dash_env):
    """maintenance_due and registrations_expiring are deferred in Jinja
    for first-paint reasons. The API is a single call, so it returns
    them -- but they must still be the same figures."""
    user, _ = dash_env
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/summary", token)

    dash = DashboardService()
    assert body["maintenance_due_count"] == dash.maintenance_due_count(user=user)
    assert body["registrations_expiring_count"] == \
        dash.registrations_expiring_count(user=user)


def test_summary_reports_availability_as_a_real_ratio(db, client, dash_env):
    """Availability is derived from real status counts, never invented."""
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/summary", token)
    assert 0.0 <= body["availability_percentage"] <= 100.0


def test_summary_availability_is_null_for_an_empty_fleet(db, client, dash_env):
    """A 0/0 fleet has no meaningful availability. Returning 0.0 would
    read as 'nothing is available', which is a different and alarming
    claim. Absent is honest; the UI renders a dash."""
    from app.modules.master_data.vehicle.models import Vehicle
    Vehicle.query.delete()
    db.session.commit()

    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/summary", token)
    assert body["availability_percentage"] is None


# ── Fleet status ────────────────────────────────────────────────────────────

def test_fleet_status_matches_the_analytics_service(db, client, dash_env):
    user, _ = dash_env
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/fleet-status", token)
    assert status == 200
    assert body == DashboardAnalyticsService().fleet_by_status(user=user)


def test_fleet_status_uses_real_backend_status_codes(db, client, dash_env):
    """Labels must be the real enum values so the frontend can map them
    to its own display text -- not invented words like 'Unavailable'."""
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/fleet-status", token)
    assert set(body["labels"]) <= {"ACTIVE", "INACTIVE", "IN_REPAIR",
                                   "DISPOSED"}


# ── Due maintenance ─────────────────────────────────────────────────────────

def test_due_maintenance_returns_a_list_with_the_documented_fields(
        db, client, dash_env):
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/due-maintenance", token)
    assert status == 200
    assert isinstance(body["items"], list)
    for item in body["items"]:
        assert set(item) >= {"vehicle_id", "plate_number", "status",
                             "current_odometer", "due_odometer", "due_date",
                             "branch", "maintenance_type"}


def test_due_maintenance_respects_the_limit_parameter(db, client, dash_env):
    token = _token(client)
    _, body = _get(client, "/api/v1/dashboard/due-maintenance?limit=1", token)
    assert len(body["items"]) <= 1


# ── Branches (for the dashboard filter) ─────────────────────────────────────

def test_branches_returns_only_branches_the_user_can_see(db, client, dash_env):
    """The frontend branch filter must be populated from the backend, so
    it cannot offer a branch the API would then refuse to answer for."""
    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/branches", token)
    assert status == 200
    assert isinstance(body["items"], list)
    assert all({"id", "name"} <= set(b) for b in body["items"])


# ── Branch scoping ──────────────────────────────────────────────────────────

def test_branch_filter_narrows_the_summary(db, client, dash_env):
    """?branch_id= must actually filter, not be silently ignored."""
    _, branch = dash_env
    token = _token(client)
    _, unfiltered = _get(client, "/api/v1/dashboard/summary", token)
    _, filtered = _get(
        client, f"/api/v1/dashboard/summary?branch_id={branch.id}", token)
    assert filtered["fleet_count"] <= unfiltered["fleet_count"]


def test_unknown_branch_id_is_rejected_not_ignored(db, client, dash_env):
    """Silently ignoring an unknown branch would show company-wide
    figures to someone who believes they are looking at one branch."""
    token = _token(client)
    r = client.get("/api/v1/dashboard/summary?branch_id=999999",
                   headers=_auth(token))
    assert r.status_code == 400


def test_non_numeric_branch_id_is_rejected(db, client, dash_env):
    token = _token(client)
    r = client.get("/api/v1/dashboard/summary?branch_id=abc",
                   headers=_auth(token))
    assert r.status_code == 400


# ── CORS (the React dev server is a different origin) ───────────────────────

def test_configured_origin_gets_cors_headers(db, client, dash_env, app):
    app.config["CORS_ORIGINS"] = ["http://localhost:5173"]
    token = _token(client)
    r = client.get("/api/v1/dashboard/summary",
                   headers={**_auth(token),
                            "Origin": "http://localhost:5173"})
    assert r.headers.get("Access-Control-Allow-Origin") == \
        "http://localhost:5173"


def test_unconfigured_origin_gets_no_cors_headers(db, client, dash_env, app):
    app.config["CORS_ORIGINS"] = ["http://localhost:5173"]
    token = _token(client)
    r = client.get("/api/v1/dashboard/summary",
                   headers={**_auth(token),
                            "Origin": "http://evil.example.com"})
    assert r.headers.get("Access-Control-Allow-Origin") is None


def test_preflight_is_answered_without_a_token(db, client, dash_env, app):
    """The browser sends OPTIONS before it has ever attached the
    Authorization header, so preflight must not require auth."""
    app.config["CORS_ORIGINS"] = ["http://localhost:5173"]
    r = client.options("/api/v1/dashboard/summary",
                       headers={"Origin": "http://localhost:5173",
                                "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200
    assert "Authorization" in r.headers.get("Access-Control-Allow-Headers", "")


def test_due_maintenance_carries_the_maintenance_type_id_for_deep_linking(
        db, client, dash_env):
    """Reported bug, reproduced end to end: clicking a due vehicle's
    plate number from this exact list landed on a New Maintenance
    Order with NO Maintenance Type selected and a completely unrelated
    PM Scope Template recommended (a Tires package instead of
    Preventive Maintenance Service).

    Root cause, found by reading Flask's own dashboard link builder
    (app/modules/main/routes.py's _compute_due_widgets): it passes
    maintenance_type_id=schedule.maintenance_type_id in the deep link,
    alongside vehicle_id -- so the New MO form opens already knowing
    which maintenance type this due item is FOR, and the PM
    recommendation is scoped to that type rather than searching across
    every maintenance type on the vehicle globally (which is what
    picked an unrelated Tires schedule as "more due" in absolute
    terms). This API endpoint never exposed maintenance_type_id at
    all, so the client had nothing to build a scoped deep link with.
    """
    from app.modules.master_data.reference.service import (
        MaintenanceTypeService, VehicleTypeService)
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.maintenance_config.models import PMSchedule
    from app.extensions import db as _db

    vt = VehicleTypeService().create(code="LV-DASH2", name="Light",
                                     category="LIGHT")
    branch = dash_env[1]
    mt = MaintenanceTypeService().create(code="DASH-PM", name="PM Service",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="DASH-DUE",
        plate_number="DUE-001", current_odometer=1200)
    schedule = PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                          interval_km=1000, cumulative_km=1000,
                          vehicle_type_id=vt.id)
    _db.session.add(schedule)
    _db.session.commit()

    # /dashboard/due-maintenance memoises its rows in a module-level
    # dict with a 12-second TTL. Any EARLIER test in the same run that
    # hit this endpoint leaves a populated entry behind, so a vehicle
    # seeded afterwards never appears and this test fails with "the
    # seeded vehicle should be due" -- a cache artefact, not a product
    # bug. This is also what made the same test fail in wide cross-file
    # batches while passing in isolation.
    from app.modules.api.dashboard import _DUE_CACHE
    _DUE_CACHE.clear()

    token = _token(client)
    status, body = _get(client, "/api/v1/dashboard/due-maintenance", token)
    assert status == 200
    row = next((r for r in body["items"] if r["vehicle_id"] == vehicle.id),
              None)
    assert row is not None, "the seeded vehicle should be due"
    assert row["maintenance_type_id"] == mt.id


def test_approvals_pending_count_respects_the_branch_selector(db, client,
                                                               dash_env):
    """Reported: approval figures did not tie up with the branch chosen
    in the dashboard header.

    Every other count in this summary already took branch_id --
    fleet_count, registrations_expiring_count, tire_stock_count,
    battery_stock_count -- but approvals_pending_count was called with
    the user alone, so it always reported every branch's tasks
    regardless of the selector. The same class of count/list
    disagreement already fixed once for registrations (see
    dashboard_service.registrations_expiring_count's own comment).
    """
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    from app.modules.master_data.org.service import BranchService
    from app.extensions import db as _db

    user, branch = dash_env
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="MO",
                                     requires_approval=False,
                                     auto_numbering=True)
    dt = DocumentType.query.filter_by(code="MO").first()
    other = BranchService().create(code="BR-DASHX", name="Other Dash Branch")
    _db.session.commit()

    for idx, b in enumerate([branch, other], start=1):
        inst = ApprovalInstance(document_type_id=dt.id,
                                reference_table="maintenance_orders",
                                reference_id=900 + idx, status="PENDING",
                                current_level=1, branch_id=b.id)
        _db.session.add(inst)
        _db.session.flush()
        _db.session.add(ApprovalTask(
            approval_instance_id=inst.id, level_number=1,
            document_type_id=dt.id, document_number=f"MO-90{idx}",
            reference_table="maintenance_orders", reference_id=900 + idx,
            assigned_user_id=user.id, branch_id=b.id, status="PENDING"))
    _db.session.commit()

    token = _token(client)
    _status, all_b = _get(client, "/api/v1/dashboard/summary", token)
    assert all_b["approvals_pending_count"] == 2

    _status, scoped = _get(
        client, f"/api/v1/dashboard/summary?branch_id={branch.id}", token)
    assert scoped["approvals_pending_count"] == 1


def test_awaiting_approval_items_carry_a_url_to_the_document(db, client,
                                                              dash_env):
    """Reported: the approver's queue rows are plain text -- there is no
    way to open the document being approved from the panel that exists
    precisely to tell you it needs approving.

    The row already has reference_table and reference_id; it just never
    resolved them into a link. Reuses the SAME _REACT_ROUTE_MAP the
    pending-approvals worklist already uses (app/modules/api/worklist.py)
    rather than a second mapping that could disagree about where a
    document type lives.
    """
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import DocumentTypeService
    from app.extensions import db as _db

    user, branch = dash_env
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="MO",
                                     requires_approval=False,
                                     auto_numbering=True)
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=777, status="PENDING",
                            current_level=1, branch_id=branch.id)
    _db.session.add(inst)
    _db.session.flush()
    _db.session.add(ApprovalTask(
        approval_instance_id=inst.id, level_number=1, document_type_id=dt.id,
        document_number="MO-2026-000777",
        reference_table="maintenance_orders", reference_id=777,
        assigned_user_id=user.id, branch_id=branch.id, status="PENDING"))
    _db.session.commit()

    _status, body = _get(client, "/api/v1/dashboard/awaiting-approval",
                         _token(client))
    row = next(i for i in body["items"] if i["reference_id"] == 777)
    assert row["url"] == "/maintenance-orders/777"


def test_awaiting_approval_url_is_null_for_an_unbuilt_module(db, client,
                                                              dash_env):
    """Same contract as the worklist: a document type with no React
    screen yet is still LISTED -- the approver needs to know it is
    waiting -- but is not made a link to a page that does not exist."""
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import DocumentTypeService
    from app.extensions import db as _db

    user, branch = dash_env
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="MO",
                                     requires_approval=False,
                                     auto_numbering=True)
    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="trip_tickets", reference_id=888,
                            status="PENDING", current_level=1,
                            branch_id=branch.id)
    _db.session.add(inst)
    _db.session.flush()
    _db.session.add(ApprovalTask(
        approval_instance_id=inst.id, level_number=1, document_type_id=dt.id,
        document_number="TT-888", reference_table="trip_tickets",
        reference_id=888, assigned_user_id=user.id, branch_id=branch.id,
        status="PENDING"))
    _db.session.commit()

    _status, body = _get(client, "/api/v1/dashboard/awaiting-approval",
                         _token(client))
    row = next(i for i in body["items"] if i["reference_id"] == 888)
    assert row["url"] is None
    assert row["document_number"] == "TT-888"


# ── Maintenance Cost Trend (React's Analytics page, previously missing) ────
#
# Both service methods (maintenance_cost_trend, maintenance_cost_trend_
# grouped) already existed and already powered the Jinja Analytics page
# -- the JSON API simply never exposed either one. Confirmed absent by
# grep before writing anything here, not assumed missing.

def test_cost_trend_requires_a_token(db, client, dash_env):
    assert client.get(
        "/api/v1/dashboard/maintenance-cost-trend").status_code == 401


def test_cost_trend_requires_vehicle_view(db, client, dash_env):
    token = _token(client, username="nodash")
    assert client.get("/api/v1/dashboard/maintenance-cost-trend",
                      headers=_auth(token)).status_code == 403


def test_cost_trend_matches_the_analytics_service_exactly(db, client, dash_env):
    """Not a second implementation -- the same equality-against-the-
    service assertion this whole file uses, so this endpoint cannot
    quietly drift from what the Jinja Analytics page already shows."""
    user, _branch = dash_env
    status, body = _get(client, "/api/v1/dashboard/maintenance-cost-trend",
                        _token(client))
    assert status == 200
    expected = DashboardAnalyticsService().maintenance_cost_trend(user=user)
    assert body == expected


def test_cost_trend_honours_a_months_param(db, client, dash_env):
    user, _branch = dash_env
    _status, body = _get(
        client, "/api/v1/dashboard/maintenance-cost-trend?months=3",
        _token(client))
    expected = DashboardAnalyticsService().maintenance_cost_trend(
        months=3, user=user)
    assert body == expected
    assert len(body["labels"]) == 3


def test_cost_trend_grouped_matches_the_service_exactly(db, client, dash_env):
    user, _branch = dash_env
    status, body = _get(
        client, "/api/v1/dashboard/maintenance-cost-trend-grouped",
        _token(client))
    assert status == 200
    expected = DashboardAnalyticsService().maintenance_cost_trend_grouped(
        user=user, group_by="BRANCH", branch_id=None)
    # `branches` is appended by the ROUTE (visible branches for the
    # drill-down selector), not by the service method -- checked
    # separately below rather than folded into this equality, the same
    # way the route itself keeps the two concerns apart.
    for key in expected:
        assert body[key] == expected[key]


def test_cost_trend_grouped_carries_the_branch_list_for_drilldown(db, client, dash_env):
    _status, body = _get(
        client, "/api/v1/dashboard/maintenance-cost-trend-grouped",
        _token(client))
    assert isinstance(body.get("branches"), list)
    assert any(b["name"] for b in body["branches"])


def test_cost_trend_grouped_by_department_requires_a_branch(db, client, dash_env):
    """The service itself refuses this combination -- department codes
    repeat across branches, and mixing them without a branch to scope
    to would silently merge two different departments that share a
    code. The API must surface that refusal, not paper over it."""
    _status, body = _get(
        client,
        "/api/v1/dashboard/maintenance-cost-trend-grouped?group_by=DEPARTMENT",
        _token(client))
    assert body.get("error")
    assert body["datasets"] == []


def test_cost_trend_grouped_department_works_with_a_branch(db, client, dash_env):
    user, branch = dash_env
    _status, body = _get(
        client,
        f"/api/v1/dashboard/maintenance-cost-trend-grouped"
        f"?group_by=DEPARTMENT&branch_id={branch.id}",
        _token(client))
    expected = DashboardAnalyticsService().maintenance_cost_trend_grouped(
        user=user, group_by="DEPARTMENT", branch_id=branch.id)
    assert body["labels"] == expected["labels"]
    assert body["datasets"] == expected["datasets"]
    assert not body.get("error")


def test_cost_trend_grouped_bad_branch_id_is_a_clean_400(db, client, dash_env):
    status, _ = _get(
        client,
        "/api/v1/dashboard/maintenance-cost-trend-grouped?branch_id=abc",
        _token(client))
    assert status == 400
