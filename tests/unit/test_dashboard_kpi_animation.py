"""Dashboard KPI card entrance/hover animation.

Ported from an uploaded React/Framer Motion dashboard template: the
same staggered fade-up entrance and springy hover lift, reimplemented
as plain CSS so no new framework or build pipeline was needed to get
the same visual result.
"""


def test_kpi_entrance_animation_and_stagger_delays_are_defined():
    css = open("app/static/css/theme-exec.css").read()
    assert "@keyframes ent-kpi-in" in css
    # At least the 6 currently-shown cards each get a distinct delay,
    # so they visibly appear one after another rather than all at once.
    for n in range(1, 7):
        assert f".ent-kpi-grid .ent-kpi:nth-child({n})" in css


def test_reduced_motion_preference_is_respected():
    """The animation must not run for anyone with the OS-level 'reduce
    motion' accessibility setting enabled."""
    css = open("app/static/css/theme-exec.css").read()
    assert "prefers-reduced-motion: reduce" in css
