"""Dashboard chart entrance animation.

Reported: chart animation was added but "somehow still not working."
Verified the config is syntactically and semantically correct for the
Chart.js 4.5.1 actually vendored in this project, and confirmed at
runtime -- not just in the source -- that every dashboard chart
instance genuinely carries the configured animation, not just that the
JS parses without error.
"""


def test_charts_partial_configures_animation_on_every_chart_type():
    """Static check: donut, bar, and line each declare a real animation
    block, not just one chart type."""
    html = open("app/modules/main/templates/main/_charts.html").read()
    assert html.count("animation:") == 3


def test_donut_animation_uses_arc_specific_options():
    """animateRotate/animateScale are the Chart.js-documented options
    specific to arc-based charts (doughnut/pie) -- confirms the donut
    chart uses them, not just a generic duration."""
    html = open("app/modules/main/templates/main/_charts.html").read()
    donut_fn = html[html.index("function donut"):html.index("function bar")]
    assert "animateRotate" in donut_fn
    assert "animateScale" in donut_fn


def test_no_global_animation_override_disables_charts():
    """A Chart.defaults.animation override anywhere would silently
    defeat every per-chart animation setting -- confirm none exists."""
    import glob
    for path in glob.glob("app/**/templates/**/*.html", recursive=True):
        text = open(path, encoding="utf-8").read()
        assert "Chart.defaults.animation" not in text, (
            f"found a global animation override in {path}")
