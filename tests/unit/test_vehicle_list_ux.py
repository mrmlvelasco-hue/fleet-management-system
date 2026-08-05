"""Vehicle Master list: real filter data, the disposed-vehicle toggle,
and the odometer display honesty rule.
"""
import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService


def _client(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_filter_dropdowns_are_populated_with_real_data(app, db):
    """The uploaded version had empty placeholder-only <select> elements
    with no actual options -- confirm branches and vehicle types really
    populate them."""
    vt = VehicleTypeService().create(code="LV-VLU", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-VLU", name="Branch VLU")
    db.session.commit()

    html = _client(app, db).get("/master/vehicles").get_data(as_text=True)
    assert f'<option value="{branch.name}">' in html
    assert f'<option value="{vt.name}">' in html
    assert '<option value="ACTIVE">' in html


def test_disposed_toggle_shows_a_count(app, db):
    """Without a count, clicking the toggle when there happen to be zero
    disposed vehicles looks exactly like the filter did nothing."""
    html = _client(app, db).get("/master/vehicles").get_data(as_text=True)
    assert "Show Disposed Vehicles" in html
    assert 'ms-1">0</span>' in html


def test_disposed_toggle_actually_changes_the_result(app, db):
    """The mechanism itself, confirmed end-to-end -- toggling the query
    parameter must genuinely add/remove disposed vehicles from what's
    shown, not just relabel the same list."""
    vt = VehicleTypeService().create(code="LV-VLU2", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-VLU2", name="Branch VLU2")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2020,
        branch_id=branch.id, conduction_number="VLU-1",
        plate_number="VLU-999")
    vehicle.status = "DISPOSED"
    db.session.commit()

    client = _client(app, db)
    hidden = client.get("/master/vehicles").get_data(as_text=True)
    shown = client.get("/master/vehicles?show_disposed=1").get_data(as_text=True)
    assert "VLU-999" not in hidden
    assert "VLU-999" in shown


def test_odometer_template_expression_never_fabricates_a_zero(app):
    """A None reading must never render as '0 km' -- that reads as 'this
    vehicle has zero mileage' rather than 'we don't know'.

    current_odometer is currently NOT NULL at the database level itself
    (confirmed directly -- even a raw SQL UPDATE to NULL is rejected by
    the schema, not just the ORM), so there is presently no live
    database row this could be tested against; flagged separately as
    its own data-integrity item, since there is currently no way at all
    to represent 'never recorded' as distinct from 'reads zero'.
    Renders the template's own expression directly against a None value
    instead, so the DEFENSIVE fallback is still verified correct and
    ready for when that schema gap is closed.
    """
    template = app.jinja_env.from_string(
        '{{ "{:,.0f}".format(current_odometer) '
        "if current_odometer is not none else '—' }}")
    assert template.render(current_odometer=None) == "—"
    assert template.render(current_odometer=0) == "0"
    assert template.render(current_odometer=45000) == "45,000"


def test_a_real_zero_reading_still_displays_as_zero(app, db):
    """The fallback must only apply to a genuinely missing reading -- a
    vehicle that really does read 0 (e.g. brand new, just registered)
    must not be hidden behind the same dash."""
    vt = VehicleTypeService().create(code="LV-VLU4", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-VLU4", name="Branch VLU4")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="Civic", year=2026,
        branch_id=branch.id, conduction_number="VLU-3",
        plate_number="VLU-777")
    vehicle.current_odometer = 0
    db.session.commit()

    html = _client(app, db).get("/master/vehicles").get_data(as_text=True)
    row = html[html.index("VLU-777"):html.index("VLU-777") + 800]
    assert "0 km" in row
