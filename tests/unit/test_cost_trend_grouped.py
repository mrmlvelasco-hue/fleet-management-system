"""Maintenance Cost Trend grouped by branch, with a department
drill-down.

Cost is attributed through the VEHICLE's branch/department -- the same
chain the Cost Center uses -- so this chart and the cost centre on a
Maintenance Order can never disagree about which department an expense
belongs to.
"""
from datetime import date

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def cost_world(app, db):
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.models import User

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    admin = User.query.filter_by(username="admin").first()

    b1 = BranchService().create(code="BR-C1", name="Alpha")
    b2 = BranchService().create(code="BR-C2", name="Beta")
    d1 = DepartmentService().create(code="D1", name="Ops Alpha",
                                    branch_id=b1.id, cost_center="CC1")
    d2 = DepartmentService().create(code="D2", name="Logistics Alpha",
                                    branch_id=b1.id, cost_center="CC2")
    vt = VehicleTypeService().create(code="LV-C", name="Light",
                                     category="LIGHT")
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="T", model="H",
                                 year=2022, branch_id=b1.id,
                                 department_id=d1.id, conduction_number="C1")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="T", model="H",
                                 year=2022, branch_id=b1.id,
                                 department_id=d2.id, conduction_number="C2")
    v3 = VehicleService().create(vehicle_type_id=vt.id, brand="T", model="H",
                                 year=2022, branch_id=b2.id,
                                 conduction_number="C3")
    today = date.today()
    for v, cost in ((v1, 1000), (v2, 2000), (v3, 500)):
        db.session.add(MaintenanceOrder(
            vehicle_id=v.id, order_category="MAINTENANCE", status="COMPLETED",
            scheduled_date=today, completed_date=today,
            actual_cost=cost, requested_by=admin.id))
    db.session.commit()
    return {"b1": b1, "b2": b2, "d1": d1, "d2": d2}


def _svc():
    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    return DashboardAnalyticsService()


def test_branch_grouping_returns_one_series_per_branch(app, db, cost_world):
    r = _svc().maintenance_cost_trend_grouped(group_by="BRANCH")
    labels = {ds["label"] for ds in r["datasets"]}
    assert labels == {"Alpha", "Beta"}


def test_department_grouping_requires_a_branch(app, db, cost_world):
    """Department codes repeat across branches, so a cross-branch
    department breakdown would silently merge two different
    departments. Refused rather than guessed at."""
    r = _svc().maintenance_cost_trend_grouped(group_by="DEPARTMENT")
    assert r["datasets"] == []
    assert "error" in r


def test_department_grouping_within_a_branch(app, db, cost_world):
    r = _svc().maintenance_cost_trend_grouped(
        group_by="DEPARTMENT", branch_id=cost_world["b1"].id)
    labels = {ds["label"] for ds in r["datasets"]}
    assert labels == {"Ops Alpha", "Logistics Alpha"}


def test_department_totals_sum_to_the_branch_total(app, db, cost_world):
    """The two views must agree -- a drill-down that doesn't reconcile
    with the level above it is worse than no drill-down."""
    by_branch = _svc().maintenance_cost_trend_grouped(group_by="BRANCH")
    alpha = next(ds for ds in by_branch["datasets"] if ds["label"] == "Alpha")
    by_dept = _svc().maintenance_cost_trend_grouped(
        group_by="DEPARTMENT", branch_id=cost_world["b1"].id)
    dept_total = sum(sum(ds["data"]) for ds in by_dept["datasets"])
    assert dept_total == sum(alpha["data"])


def test_every_series_has_a_value_for_every_month(app, db, cost_world):
    """Months with no spend must be 0, not omitted, or the lines
    misalign across the x-axis."""
    r = _svc().maintenance_cost_trend_grouped(group_by="BRANCH", months=6)
    assert len(r["labels"]) == 6
    for ds in r["datasets"]:
        assert len(ds["data"]) == 6


def test_series_are_ordered_by_spend(app, db, cost_world):
    r = _svc().maintenance_cost_trend_grouped(group_by="BRANCH")
    totals = [sum(ds["data"]) for ds in r["datasets"]]
    assert totals == sorted(totals, reverse=True)


def test_adjacent_series_get_visually_distinct_colours(app, db, cost_world):
    """The default palette opens with two dark blues, which are fine as
    bars but indistinguishable as overlapping lines."""
    r = _svc().maintenance_cost_trend_grouped(group_by="BRANCH")
    colours = [ds["color"] for ds in r["datasets"]]
    assert len(colours) == len(set(colours))
    assert colours[0] != colours[1]


def test_endpoint_returns_branches_the_user_can_see(app, db, cost_world):
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
                follow_redirects=True)
    payload = client.get("/dashboard/cost-trend-grouped").get_json()
    names = {b["name"] for b in payload["branches"]}
    assert "Alpha" in names and "Beta" in names


def test_endpoint_supports_the_department_drilldown(app, db, cost_world):
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
                follow_redirects=True)
    payload = client.get(
        f"/dashboard/cost-trend-grouped?group_by=DEPARTMENT"
        f"&branch_id={cost_world['b1'].id}").get_json()
    assert payload["group_by"] == "DEPARTMENT"
    assert {ds["label"] for ds in payload["datasets"]} == {
        "Ops Alpha", "Logistics Alpha"}
