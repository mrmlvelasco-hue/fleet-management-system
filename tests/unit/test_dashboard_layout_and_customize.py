"""Dashboard layout reorganization (Registration Status / Maintenance
Management / Approval Workflow grouped together, the two "due" lists
paired below them with real pagination), and the Customize feature
extended to actually cover every panel added since its catalog was
last updated.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin, _seed_dashboard_widgets


def _client(app, db):
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    _seed_dashboard_widgets()
    db.session.commit()
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


# ── Layout ────────────────────────────────────────────────────────────────

def test_registration_status_maintenance_management_and_approval_workflow_are_grouped(
        app, db):
    """The three panels requested to sit together in one row appear in
    that order, with no other panel's heading between the first and
    the last."""
    html = _client(app, db).get("/").get_data(as_text=True)
    pos_reg = html.index('ent-panel__title">Vehicle Registration Status')
    pos_mm = html.index('ent-panel__title">Maintenance Management')
    pos_aw = html.index('ent-panel__title">Approval Workflow')
    assert pos_reg < pos_mm < pos_aw
    between = html[pos_reg:pos_aw]
    assert between.count("ent-panel__title") == 2  # only these two headings


def test_due_lists_are_paired_directly_after_the_summary_row(app, db):
    html = _client(app, db).get("/").get_data(as_text=True)
    pos_aw = html.index('ent-panel__title">Approval Workflow')
    pos_due_reg = html.index(
        'ent-panel__title">Vehicles Due for Registration Renewal')
    pos_due_mnt = html.index(
        'ent-panel__title">Vehicles Due for Maintenance')
    pos_fma = html.index('ent-panel__title">For My Action')
    assert pos_aw < pos_due_reg < pos_fma
    assert pos_aw < pos_due_mnt < pos_fma


def test_maintenance_management_panel_renders_its_own_canvas(app, db):
    html = _client(app, db).get("/").get_data(as_text=True)
    assert 'id="chartMaintenanceManagement"' in html


def test_duplicate_mo_by_type_chart_is_hidden_on_the_compact_dashboard(
        app, db):
    """Confirms the de-duplication: the dashboard's own inclusion of
    _charts.html (compact=true) does not also render the older
    Maintenance Orders by Type card now that Maintenance Management
    covers the same data."""
    html = _client(app, db).get("/").get_data(as_text=True)
    assert '<canvas id="chartMoByType">' not in html
    assert '<h6 class="card-title mb-3">\n        Maintenance Orders by Type' not in html


def test_mo_by_type_chart_still_exists_on_the_standalone_analytics_page(
        app, db):
    """The shared partial must not have lost this chart entirely --
    only hidden on the dashboard specifically."""
    client = _client(app, db)
    html = client.get("/admin/analytics").get_data(as_text=True)
    assert "Maintenance Orders by Type" in html


# ── Pagination ───────────────────────────────────────────────────────────

def test_due_maintenance_partial_has_pagination_markup():
    content = open(
        "app/modules/main/templates/main/_due_maintenance.html").read()
    assert 'id="dueMaintenanceTable"' in content
    assert 'due-page-row' in content
    assert 'data-pager-for="dueMaintenanceTable"' in content


def test_due_registration_partial_has_pagination_markup():
    content = open(
        "app/modules/main/templates/main/_due_registration.html").read()
    assert 'id="dueRegistrationTable"' in content
    assert 'due-page-row' in content
    assert 'data-pager-for="dueRegistrationTable"' in content


def test_due_maintenance_widget_uses_innerhtml_not_outerhtml():
    """The real bug fixed this round: the maintenance widget's
    container is now the panel BODY (same as registration), so
    replacing it with outerHTML would tear out the panel's header and
    footer along with it."""
    content = open(
        "app/modules/main/templates/main/dashboard.html").read()
    assert 'm.innerHTML = data.due_maintenance_html' in content
    assert 'm.outerHTML = data.due_maintenance_html' not in content


# ── Customize ────────────────────────────────────────────────────────────

def test_customize_page_offers_every_panel_added_since_the_catalog_was_last_updated(
        app, db):
    html = _client(app, db).get(
        "/admin/dashboard-config").get_data(as_text=True)
    for label in ("Vehicle Registration Status", "Maintenance Management",
                 "Approval Workflow", "Security and Compliance",
                 "Fuel Management", "Analytics"):
        assert label in html


def test_hiding_a_panel_through_the_real_customize_form_actually_hides_it(
        app, db):
    """The actual regression this round fixes: these panels previously
    had no gating at all, so toggling them in Customize had zero
    effect no matter what was submitted."""
    client = _client(app, db)
    all_codes = ["FLEET", "MAINTENANCE", "APPROVALS", "REGISTRATIONS",
                "TIRES", "BATTERIES", "MY_ACTIONS", "VEHICLE_LIST",
                "DUE_MAINTENANCE", "DUE_REGISTRATION",
                "REGISTRATION_STATUS", "MAINTENANCE_MANAGEMENT",
                "APPROVAL_WORKFLOW", "FUEL_MANAGEMENT"]
    # SECURITY_COMPLIANCE and ANALYTICS deliberately omitted.
    client.post("/admin/dashboard-config",
               data={"visible_widgets": all_codes}, follow_redirects=True)
    html = client.get("/").get_data(as_text=True)
    assert 'ent-panel__title">Security and Compliance' not in html
    assert '<h5 class="mt-4 mb-3">Analytics</h5>' not in html
    # Everything else the user left checked must still show.
    assert 'ent-panel__title">Fuel Management' in html
    assert 'ent-panel__title">Vehicle Registration Status' in html
    assert 'ent-panel__title">Maintenance Management' in html
    assert 'ent-panel__title">Approval Workflow' in html


def test_hidden_fuel_panel_does_not_run_its_query(app, db, monkeypatch):
    """A hidden panel shouldn't just be hidden visually -- its data
    shouldn't be computed at all."""
    client = _client(app, db)
    all_codes = ["FLEET", "MAINTENANCE", "APPROVALS", "REGISTRATIONS",
                "TIRES", "BATTERIES", "MY_ACTIONS", "VEHICLE_LIST",
                "DUE_MAINTENANCE", "DUE_REGISTRATION",
                "REGISTRATION_STATUS", "MAINTENANCE_MANAGEMENT",
                "APPROVAL_WORKFLOW", "SECURITY_COMPLIANCE", "ANALYTICS"]
    # FUEL_MANAGEMENT deliberately omitted.
    client.post("/admin/dashboard-config",
               data={"visible_widgets": all_codes}, follow_redirects=True)

    called = {"value": False}
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService
    original = FuelAnalyticsService.summary

    def spy(self, *a, **kw):
        called["value"] = True
        return original(self, *a, **kw)
    monkeypatch.setattr(FuelAnalyticsService, "summary", spy)

    client.get("/")
    assert called["value"] is False

    # Restore for other tests in this session.
    all_codes.append("FUEL_MANAGEMENT")
    client.post("/admin/dashboard-config",
               data={"visible_widgets": all_codes}, follow_redirects=True)


def test_an_install_without_flask_seed_all_still_shows_every_panel(app, db):
    """Backward compatibility: an install where these new widget rows
    were never seeded must not silently lose panels it always had --
    the same 'unregistered = show it' rule already used throughout
    this dashboard."""
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    # Deliberately NOT calling _seed_dashboard_widgets() -- the catalog
    # is empty for the six new panel codes.
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"})
    html = c.get("/").get_data(as_text=True)
    assert 'ent-panel__title">Security and Compliance' in html
    assert 'ent-panel__title">Vehicle Registration Status' in html
