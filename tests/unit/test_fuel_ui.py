"""Fuel Management screens and wiring.

Reported: fuel had an engine (odometer validation, consumption,
anomalies) and routes, but was reachable nowhere -- the permissions the
routes required were never registered (so Roles had nothing to assign),
there was no sidebar link, and none of the four templates the routes
render actually existed, meaning every route 500'd the moment it was
opened rather than simply being invisible.
"""
from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_fuel_permissions_are_registered(db):
    """Without this, Roles had nothing to assign -- the permission codes
    the routes required simply didn't exist anywhere."""
    sync_permissions()
    db.session.commit()
    from app.modules.user_management.models import Permission
    codes = {p.code for p in Permission.query.all()}
    assert {"fuel.view", "fuel.create", "fuel.update"} <= codes


def test_fuel_link_appears_in_the_sidebar(app, db):
    client = _client(app, db)
    html = client.get("/").get_data(as_text=True)
    assert "Fuel Management" in html
    assert "/transactions/fuel" in html


def test_every_fuel_route_renders(app, db):
    """Each of these previously raised TemplateNotFound -- the
    templates the routes reference were never created."""
    client = _client(app, db)
    for url in ("/transactions/fuel",
               "/transactions/fuel?view=flagged",
               "/transactions/fuel/analytics",
               "/transactions/fuel/new",
               "/transactions/fuel/import"):
        r = client.get(url)
        assert r.status_code == 200, f"{url} -> {r.status_code}"


def test_creating_a_transaction_through_the_real_form_succeeds(app, db):
    """End-to-end through the actual POST, not just checking the page
    loads -- this is what caught the datetime-local format bug below."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="LV-FUI", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-FUI", name="Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="FUI-1")
    db.session.commit()

    client = _client(app, db)
    r = client.post("/transactions/fuel/new", data={
        "vehicle_id": vehicle.id,
        "transaction_date": "2026-08-05T09:30",   # what <input type=
        "station": "Petron EDSA", "fuel_type": "DIESEL",
        "litres": "45.5", "total_amount": "2827.83",
        "odometer_reported": "65430"}, follow_redirects=True)
    assert r.status_code == 200

    from app.modules.transactions.fuel.models import FuelTransaction
    txn = FuelTransaction.query.filter_by(station="Petron EDSA").first()
    assert txn is not None, "the transaction was never saved"
    assert txn.odometer_status == "OK"


def test_datetime_local_input_format_is_accepted(db):
    """The regression itself: every browser's <input type="datetime-
    local"> submits YYYY-MM-DDTHH:MM with a literal T separator, which
    the importer's date parser didn't recognise -- so the manual entry
    form always failed with 'not a recognisable date', 100% of the
    time, for every single submission."""
    from app.modules.transactions.fuel.import_export import _to_datetime
    from datetime import datetime
    result = _to_datetime("2026-08-05T09:30")
    assert result == datetime(2026, 8, 5, 9, 30)
