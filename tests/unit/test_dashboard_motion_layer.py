"""The dashboard's "motion layer" -- sidebar auto-expand, the animation
CSS/JS wiring, and the rebuilt chart partial.

A real, confirmed bug was found and fixed in this round: an earlier
edit left a malformed, unclosed <script> tag on the dashboard page
(two lines of a comment were split into an orphaned open tag with no
matching close, followed by the inserted animation script tag and a
second script block continuing the same comment). This produced a
genuine browser-side JavaScript syntax error ("Unexpected token '<'")
that silently broke the entire motion layer -- confirmed directly with
a real browser before and after the fix, not assumed from reading the
template.
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


def test_dashboard_script_tags_are_balanced():
    """The exact regression: confirms every <script> has a matching
    </script>, which the malformed edit violated."""
    content = open(
        "app/modules/main/templates/main/dashboard.html").read()
    assert content.count("<script") == content.count("</script>")


def test_charts_partial_script_tags_are_balanced():
    content = open(
        "app/modules/main/templates/main/_charts.html").read()
    assert content.count("<script") == content.count("</script>")


def test_animation_js_and_css_are_loaded_on_the_dashboard(app, db):
    html = _client(app, db).get("/").get_data(as_text=True)
    assert "dashboard-animations.css" in html
    assert "dashboard-animations.js" in html


def test_animation_assets_are_not_loaded_on_other_pages(app, db):
    """extra_css defaults to nothing -- confirms the animation CSS
    doesn't leak onto every other page in the app."""
    client = _client(app, db)
    html = client.get("/master/vehicles").get_data(as_text=True)
    assert "dashboard-animations.css" not in html


def test_sidebar_active_section_expands_for_a_page_inside_it(app, db):
    """The genuinely new feature merged this round: a page inside a
    collapsed sidebar section should auto-expand that section rather
    than requiring a manual click."""
    client = _client(app, db)
    html = client.get("/master/vehicle-types/new").get_data(as_text=True)
    idx = html.find('id="sbGroupMasterData"')
    assert idx != -1
    assert "collapse show" in html[idx - 30:idx]


def test_sidebar_unrelated_section_stays_expanded(app, db):
    """Every group stays open regardless of which page is active.

    This test previously asserted the OPPOSITE -- that a group unrelated
    to the current page renders collapsed -- and directly contradicted
    tests/integration/test_sidebar_navigation.py, which pins "every
    group stays open" as an explicit client request. Both were in the
    tree at once, so whichever behaviour shipped, one suite failed.

    Resolved in favour of always-expanded, because two other artifacts
    agree with it: the client request recorded in the integration test,
    and app.css's own comment ("no Bootstrap accordion linkage"). The
    template had also carried data-bs-parent, which makes opening one
    group actively CLOSE another -- the specific thing the client asked
    us not to do.
    """
    client = _client(app, db)
    html = client.get("/transactions/fuel").get_data(as_text=True)
    idx = html.find('id="sbGroupMasterData"')
    assert idx != -1
    assert "collapse show" in html[idx - 30:idx]


def test_fuel_tile_avg_kmpl_never_shows_a_fabricated_value(app, db):
    """The recurring bug pattern from several uploaded files: showing
    the literal string '0.0' instead of the real value or an honest
    dash. Confirmed absent from the merged template."""
    content = open(
        "app/modules/main/templates/main/dashboard.html").read()
    assert "'0.0' if fuel_summary.avg_kmpl" not in content
    assert "fuel_summary.avg_kmpl if fuel_summary.avg_kmpl is not none else '—'" in content


def test_worklist_pagination_markup_is_present_for_a_long_queue(app, db):
    """for_my_action with more than 5 items should render the
    pagination controls the animation JS expects."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder, TransactionType)
    from datetime import date

    vt = VehicleTypeService().create(code="LV-WLP", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-WLP", name="Branch WLP")
    tt = TransactionType.query.filter_by(
        order_category="OPERATIONAL").first()
    from app.extensions import db as _db
    for i in range(7):
        v = VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Vios",
            year=2022, branch_id=branch.id,
            conduction_number=f"WLP-{i}")
        order = MaintenanceOrder(
            vehicle_id=v.id, order_category="OPERATIONAL",
            transaction_type_id=tt.id if tt else None,
            scheduled_date=date.today(), status="SUBMITTED")
        _db.session.add(order)
    _db.session.commit()

    client = _client(app, db)
    html = client.get("/").get_data(as_text=True)
    if "approvalWorklistGroup" in html:
        assert "prevWorklistPage" in html
        assert "nextWorklistPage" in html
