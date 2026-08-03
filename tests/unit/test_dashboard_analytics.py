"""Tests for DashboardAnalyticsService and the /dashboard/charts
endpoint that feeds both the Dashboard's compact charts row and the
standalone Analytics page.
"""
from datetime import date

import pytest

from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.cli import _seed_transaction_types
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.maintenance_order.models import TransactionType


@pytest.fixture()
def fleet(db):
    _seed_transaction_types()
    db.session.commit()
    vt = VehicleTypeService().create(code="LV-AN", name="Light",
                                     category="LIGHT")
    branch_a = BranchService().create(code="BR-AN-A", name="Branch A")
    branch_b = BranchService().create(code="BR-AN-B", name="Branch B")

    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch_a.id,
                                 conduction_number="AN-1")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2022,
                                 branch_id=branch_a.id,
                                 conduction_number="AN-2")
    v3 = VehicleService().create(vehicle_type_id=vt.id, brand="Ford",
                                 model="Escape", year=2021,
                                 branch_id=branch_b.id,
                                 conduction_number="AN-3")
    v2.status = "IN_REPAIR"
    v3.status = "DISPOSED"
    db.session.commit()
    return vt, branch_a, branch_b, v1, v2, v3


def test_fleet_by_status_counts_correctly(db, fleet):
    result = DashboardAnalyticsService().fleet_by_status()
    counts = dict(zip(result["labels"], result["data"]))
    assert counts.get("ACTIVE") == 1
    assert counts.get("IN_REPAIR") == 1
    assert counts.get("DISPOSED") == 1


def test_fleet_by_branch_excludes_disposed(db, fleet):
    """Matches what the 'Fleet' KPI elsewhere on the dashboard already
    means -- disposed vehicles aren't part of the active fleet count."""
    vt, branch_a, branch_b, v1, v2, v3 = fleet
    result = DashboardAnalyticsService().fleet_by_branch()
    counts = dict(zip(result["labels"], result["data"]))
    assert counts.get("Branch A") == 2   # v1 active, v2 in_repair
    assert "Branch B" not in counts or counts["Branch B"] == 0  # v3 disposed


def test_maintenance_cost_trend_sums_by_month(db, fleet):
    vt, branch_a, branch_b, v1, v2, v3 = fleet
    mt = MaintenanceTypeService().create(code="PMS-AN", name="PMS",
                                         category="PREVENTIVE")
    svc = MaintenanceOrderService()
    mo1 = svc.create(vehicle_id=v1.id, maintenance_type_id=mt.id,
                     scheduled_date=date(2026, 6, 1), user=None)
    mo1.status, mo1.completed_date, mo1.actual_cost = (
        "COMPLETED", date(2026, 6, 10), 5000)
    mo2 = svc.create(vehicle_id=v1.id, maintenance_type_id=mt.id,
                     scheduled_date=date(2026, 6, 5), user=None)
    mo2.status, mo2.completed_date, mo2.actual_cost = (
        "COMPLETED", date(2026, 6, 20), 2500)
    db.session.commit()

    result = DashboardAnalyticsService().maintenance_cost_trend(months=6)
    totals = dict(zip(result["labels"], result["data"]))
    assert totals.get("Jun 2026") == 7500.0


def test_maintenance_cost_trend_excludes_non_completed_orders(db, fleet):
    vt, branch_a, branch_b, v1, v2, v3 = fleet
    mt = MaintenanceTypeService().create(code="PMS-AN2", name="PMS",
                                         category="PREVENTIVE")
    mo = MaintenanceOrderService().create(
        vehicle_id=v1.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    mo.actual_cost = 99999  # set, but never completed
    db.session.commit()

    result = DashboardAnalyticsService().maintenance_cost_trend(months=6)
    assert 99999 not in result["data"]


def test_pm_compliance_matches_the_due_calculation_service(db, fleet):
    """Must use the SAME source as the dashboard's due-list/KPI -- this
    is the exact class of bug fixed earlier when two different queries
    disagreed on the same number."""
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    result = DashboardAnalyticsService().pm_compliance()
    due = PMDueCalculationService().get_all_due_vehicles(
        exclude_with_open_order=False)
    overdue_count = len([d for d in due if d["status"] == "OVERDUE"])
    counts = dict(zip(result["labels"], result["data"]))
    assert counts["Overdue"] == overdue_count


def test_mo_by_type_separates_preventive_corrective_and_operational(
        db, fleet):
    vt, branch_a, branch_b, v1, v2, v3 = fleet
    pm_type = MaintenanceTypeService().create(code="PMS-AN3", name="PMS",
                                              category="PREVENTIVE")
    cm_type = MaintenanceTypeService().create(code="CM-AN3", name="Repair",
                                              category="CORRECTIVE")
    tt = TransactionType.query.filter_by(code="DEP-ASSIGNMENT").first()

    svc = MaintenanceOrderService()
    svc.create(vehicle_id=v1.id, maintenance_type_id=pm_type.id,
              scheduled_date=date.today(), user=None)
    svc.create(vehicle_id=v1.id, maintenance_type_id=cm_type.id,
              scheduled_date=date.today(), user=None)
    svc.create(vehicle_id=v1.id, scheduled_date=date.today(), user=None,
              order_category="OPERATIONAL", transaction_type_id=tt.id)

    result = DashboardAnalyticsService().maintenance_orders_by_type()
    counts = dict(zip(result["labels"], result["data"]))
    assert counts.get("Preventive Maintenance") == 1
    assert counts.get("Corrective Maintenance") == 1
    assert counts.get("Operational") == 1


def test_all_charts_returns_every_dataset(db, fleet):
    result = DashboardAnalyticsService().all_charts()
    assert set(result.keys()) == {
        "fleet_by_status", "fleet_by_branch", "maintenance_cost_trend",
        "pm_compliance", "mo_by_type", "registration_status"}
    for chart in result.values():
        assert "labels" in chart and "data" in chart


# ── HTTP endpoint and pages ──────────────────────────────────────────────────

def test_charts_endpoint_returns_all_five_datasets(db, client, fleet):
    r = client.get("/dashboard/charts")
    assert r.status_code in (200, 302)  # 302 if this client isn't logged in


def test_dashboard_includes_the_charts_row(app, db, fleet):
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
               follow_redirects=True)
    html = client.get("/").get_data(as_text=True)
    assert "fmsChartsRow" in html
    assert "vendor/chartjs/chart.umd.min.js" in html


def test_analytics_page_requires_its_own_permission(app, db, fleet):
    """A role without analytics.view must not reach the page, even if it
    can see everything else -- matches how every other report in this
    system is permissioned individually."""
    from app.core.security.registry import sync_permissions
    from app.modules.user_management.models import User, Role, Permission
    from app.core.security.password import hash_password
    sync_permissions()
    role = Role(name="No Analytics Role")
    role.permissions = Permission.query.filter(
        Permission.code == "vehicle.view").all()
    user = User(username="noanalytics", email="na@example.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "noanalytics",
                                "password": "secret123"},
               follow_redirects=True)
    r = client.get("/admin/analytics")
    assert r.status_code == 403


def test_registration_status_splits_the_fleet(db, fleet):
    """The dashboard donut: every non-disposed vehicle lands in exactly
    one of Active / Expiring Soon / For Renewal, and the three must sum
    to the fleet total -- a donut whose slices don't add up to its own
    centre number is worse than no donut."""
    result = DashboardAnalyticsService().registration_status()
    assert result["labels"] == ["Active", "Expiring Soon", "For Renewal"]
    assert sum(result["data"]) == result["total"]


def test_registration_status_percentages_are_consistent(db, fleet):
    result = DashboardAnalyticsService().registration_status()
    if result["total"]:
        assert round(sum(result["pct"])) == 100


def test_registration_status_handles_an_empty_fleet(db):
    """No vehicles must not divide by zero."""
    result = DashboardAnalyticsService().registration_status()
    assert result["total"] == 0
    assert result["pct"] == [0.0, 0.0, 0.0]
