"""Per-chart data fetching, and PM Schedule list pagination.

Reported: "Vehicle Registration Status" and "Maintenance Management"
took ~11 seconds to appear on the dashboard, and the PM Templates list
lagged after clicking it in the sidebar.

Registration Status and Maintenance Management are individually among
the CHEAPEST charts. They were slow because all six charts were served
in one combined payload, so every chart waited for pm_compliance --
which runs the full fleet-wide PM due calculation, evaluating every
vehicle against every applicable schedule.
"""
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


# ── Per-chart fetching ──────────────────────────────────────────────────

def test_only_parameter_returns_just_that_chart(app, db):
    r = _client(app, db).get(
        "/dashboard/charts?only=registration_status")
    assert r.status_code == 200
    payload = r.get_json()
    assert list(payload.keys()) == ["registration_status"]


def test_requesting_a_cheap_chart_does_not_compute_the_expensive_one(
        app, db, monkeypatch):
    """The whole point. Asking for Registration Status must not trigger
    the fleet-wide PM due calculation."""
    from app.core.dashboard_analytics_service import DashboardAnalyticsService
    called = {"pm": False}
    original = DashboardAnalyticsService.pm_compliance

    def spy(self, *a, **kw):
        called["pm"] = True
        return original(self, *a, **kw)
    monkeypatch.setattr(DashboardAnalyticsService, "pm_compliance", spy)

    _client(app, db).get("/dashboard/charts?only=registration_status")
    assert called["pm"] is False, (
        "a cheap chart still triggered the expensive fleet-wide "
        "calculation -- the split isn't working")


def test_multiple_charts_can_be_requested_together(app, db):
    r = _client(app, db).get(
        "/dashboard/charts?only=registration_status,mo_by_type")
    keys = set(r.get_json().keys())
    assert keys == {"registration_status", "mo_by_type"}


def test_omitting_only_still_returns_every_chart(app, db):
    """Backward compatibility -- the standalone Analytics page and any
    existing caller must be unaffected."""
    payload = _client(app, db).get("/dashboard/charts").get_json()
    for key in ("fleet_by_status", "fleet_by_branch", "pm_compliance",
                "mo_by_type", "maintenance_cost_trend",
                "registration_status"):
        assert key in payload


def test_an_unknown_chart_name_is_ignored_not_fatal(app, db):
    r = _client(app, db).get("/dashboard/charts?only=not_a_real_chart")
    assert r.status_code == 200
    assert r.get_json() == {}


def test_front_end_requests_charts_individually():
    js = open("app/modules/main/templates/main/_charts.html").read()
    assert "CHART_DATA_KEY" in js
    assert "only=" in js
    # Two canvases share mo_by_type; the fetch must be de-duplicated
    # rather than requesting the same data twice.
    assert "fetchedKeys" in js


# ── PM Schedule (PM Templates) list ─────────────────────────────────────

def test_pm_schedule_list_is_paginated(app, db):
    from app.modules.maintenance_config.service import PMScheduleService
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.maintenance_config.models import PMSchedule

    mt = MaintenanceTypeService().create(code="PM-PG", name="PM Page",
                                         category="PREVENTIVE")
    db.session.add_all([
        PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                   profile_code="PRF-001", interval_km=5000,
                   profile_description=f"Toyota Vios {i} km",
                   sequence_position=i)
        for i in range(30)])
    db.session.commit()

    rows, pagination = PMScheduleService().list_paginated(page=1, per_page=25)
    assert len(rows) == 25
    assert pagination.total >= 30
    assert pagination.pages >= 2


def test_pm_schedule_search_is_server_side(app, db):
    from app.modules.maintenance_config.service import PMScheduleService
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.maintenance_config.models import PMSchedule

    mt = MaintenanceTypeService().create(code="PM-SR", name="PM Search",
                                         category="PREVENTIVE")
    db.session.add_all([
        PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                   profile_code="PRF-VIOS", interval_km=5000,
                   profile_description="Toyota Vios 5000 km",
                   sequence_position=1),
        PMSchedule(maintenance_type_id=mt.id, trigger_mode="KM",
                   profile_code="PRF-HILUX", interval_km=5000,
                   profile_description="Toyota Hilux 5000 km",
                   sequence_position=1)])
    db.session.commit()

    _rows, everything = PMScheduleService().list_paginated(page=1)
    rows, filtered = PMScheduleService().list_paginated(page=1, search="Vios")
    assert filtered.total < everything.total
    assert all("Vios" in (r.profile_description or "") for r in rows)


def test_pm_schedule_page_renders_search_and_pager(app, db):
    html = _client(app, db).get("/admin/pm-schedules").get_data(as_text=True)
    assert 'name="q"' in html
    assert "fms-datatable" not in html, (
        "client-side DataTables would only search the visible page")
