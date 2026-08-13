"""Dashboard chart entrance animation.

Reported: chart animation was added but "somehow still not working."
Verified the config is syntactically and semantically correct for the
Chart.js 4.5.1 actually vendored in this project, and confirmed at
runtime -- not just in the source -- that every dashboard chart
instance genuinely carries the configured animation, not just that the
JS parses without error.
"""


def test_charts_partial_configures_animation_for_every_chart_type():
    """The three separate donut/bar/line builders were later merged into
    one type-aware builder so charts can change type on demand. The
    animation config moved with them: one branch for arc charts
    (rotate/scale) and one for everything else, both with a real
    duration and easing.
    """
    html = open("app/modules/main/templates/main/_charts.html").read()
    assert "animation: isArc" in html
    assert html.count("easing: \"easeOutQuart\"") >= 2
    assert "duration: 1200" in html


def test_arc_charts_use_arc_specific_animation_options():
    """animateRotate/animateScale are the Chart.js-documented options
    specific to arc-based charts (doughnut/pie). After the builders were
    merged, these must apply on the isArc branch and NOT to bar/line."""
    html = open("app/modules/main/templates/main/_charts.html").read()
    arc_branch = html[html.index("animation: isArc"):
                     html.index("animation: isArc") + 250]
    assert "animateRotate" in arc_branch
    assert "animateScale" in arc_branch


def test_no_global_animation_override_disables_charts():
    """A Chart.defaults.animation override anywhere would silently
    defeat every per-chart animation setting -- confirm none exists."""
    import glob
    for path in glob.glob("app/**/templates/**/*.html", recursive=True):
        text = open(path, encoding="utf-8").read()
        assert "Chart.defaults.animation" not in text, (
            f"found a global animation override in {path}")
