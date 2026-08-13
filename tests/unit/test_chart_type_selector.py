"""Chart type selector on the analytical charts.

Requested: users want to switch a chart from bar to pie. Each chart
card now carries its own type picker (Bar / Pie / Donut / Line / Area).

Switching rebuilds the chart from the payload ALREADY fetched -- the
report is never re-run and the server is not asked for the data again,
which is what "switch without rerunning the report" requires.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin

CHART_IDS = ["chartFleetByStatus", "chartFleetByBranch", "chartPmCompliance",
             "chartMoByType", "chartCostTrend"]


def _client(app, db):
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_every_chart_card_has_its_own_type_picker(app, db):
    html = _client(app, db).get("/admin/analytics").get_data(as_text=True)
    for cid in CHART_IDS:
        assert f'data-for="{cid}"' in html, f"no picker for {cid}"


def test_all_requested_chart_types_are_offered(app, db):
    html = _client(app, db).get("/admin/analytics").get_data(as_text=True)
    for value in ("bar", "pie", "doughnut", "line", "area"):
        assert f'value="{value}"' in html


def test_switching_does_not_refetch_the_report_data():
    """The requirement was to switch WITHOUT rerunning the report. The
    change handler must rebuild from stored state, never call fetch."""
    js = open("app/modules/main/templates/main/_charts.html").read()
    start = js.index('.chart-type-picker").forEach')
    # End at the handler's own closing, not a fixed character window --
    # the initial data fetch legitimately follows this block and would
    # otherwise be caught by the assertion.
    end = js.index("});", js.index("buildChart(canvas, st.payload"))
    handler = js[start:end]
    assert "buildChart(" in handler
    assert "fetch(" not in handler, (
        "the type switch must not re-request the data")


def test_the_choice_is_remembered_between_visits():
    js = open("app/modules/main/templates/main/_charts.html").read()
    assert "localStorage" in js
    assert "rememberType" in js


def test_storage_failure_does_not_break_charts():
    """Private-browsing mode can make localStorage throw. A remembered
    preference is a nicety; charts failing to draw is not acceptable."""
    js = open("app/modules/main/templates/main/_charts.html").read()
    saved = js[js.index("function savedType"):js.index("function rememberType")]
    assert "try" in saved and "catch" in saved
    remember = js[js.index("function rememberType"):js.index("function buildChart")]
    assert "try" in remember and "catch" in remember


def test_old_fixed_type_builders_are_fully_replaced():
    """Leftover calls to the removed donut()/bar()/line() helpers would
    throw at runtime -- confirm none survive."""
    js = open("app/modules/main/templates/main/_charts.html").read()
    for stale in ["donut(canvas", "bar(canvas", "line(canvas"]:
        assert stale not in js, f"stale call to removed helper: {stale}"


def test_arc_charts_keep_a_legend_and_bar_charts_do_not():
    """A pie/donut needs a legend to identify slices; a single-series
    bar is already labelled by its axis, so a legend there is noise."""
    js = open("app/modules/main/templates/main/_charts.html").read()
    assert "display: isArc && !opts.hideLegend" in js


def test_dashboard_also_renders_pickers(app, db):
    """The dashboard includes the same partial, so the feature is
    available there too rather than only on the Analytics page."""
    html = _client(app, db).get("/").get_data(as_text=True)
    assert "chart-type-picker" in html
