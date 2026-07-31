"""Tests for mobile-responsive chart sizing.

The chart canvases and their Chart.js legend/tick text used a fixed
pixel size regardless of viewport -- fine on a desktop card, but
wasteful and cramped on a phone. Sizing moved from inline Jinja-computed
pixel styles to CSS classes specifically so a media query can shrink
them further; these tests confirm that path is actually wired up rather
than just present in the CSS file unused.
"""
from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _login(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
               follow_redirects=True)
    return client


def test_dashboard_charts_use_responsive_css_classes_not_inline_pixels(
        app, db):
    """Inline `style="height:220px"` can't be overridden by a media
    query; a CSS class can. This is what actually enables the mobile
    breakpoints below to have any effect."""
    client = _login(app, db)
    html = client.get("/").get_data(as_text=True)
    assert "fms-chart-box" in html
    assert "fms-chart-box--compact" in html
    # The old fixed inline height must be gone, not just superseded.
    assert 'style="position:relative; height:220px' not in html
    assert 'style="position:relative; height:300px' not in html


def test_mobile_and_small_phone_breakpoints_are_present(app, db):
    client = _login(app, db)
    html = client.get("/").get_data(as_text=True)
    assert "@media (max-width: 860px)" in html   # same breakpoint as the sidebar fix
    assert "@media (max-width: 420px)" in html   # small-phone tier


def test_analytics_page_shares_the_identical_responsive_markup(app, db):
    """The Dashboard's compact row and the standalone Analytics page
    must never drift into two different mobile behaviours -- they
    include the exact same partial."""
    client = _login(app, db)
    dashboard_html = client.get("/").get_data(as_text=True)
    analytics_html = client.get("/admin/analytics").get_data(as_text=True)

    def extract_style_block(html):
        start = html.index("<style>")
        end = html.index("</style>") + len("</style>")
        return html[start:end]

    assert extract_style_block(dashboard_html) == extract_style_block(
        analytics_html)


def test_chartjs_options_reduce_legend_and_tick_font_on_mobile(app, db):
    """The CSS shrinks the BOX; this confirms the JS-side shrink for
    Chart.js's own legend/axis text (which CSS alone cannot touch) is
    actually present in the shipped script, not just described in a
    comment."""
    client = _login(app, db)
    html = client.get("/").get_data(as_text=True)
    assert "isMobile()" in html
    assert "isSmallPhone()" in html
    assert "legendFontSize()" in html
    # The cost trend chart's currency axis abbreviates on mobile
    # (₱150k rather than ₱150,000) since the full form doesn't fit a
    # phone's y-axis column.
    assert '"k"' in html or "'k'" in html
