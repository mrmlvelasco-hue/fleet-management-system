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


def test_odometer_reading_is_visible_on_the_list(app, db):
    """Reported: only the status badge and a truncated note were
    visible; the actual encoded reading was only reachable by opening
    the correction modal on each row individually."""
    from datetime import datetime
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelTransaction

    vt = VehicleTypeService().create(code="LV-ODOL", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-ODOL", name="Branch ODOL")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="ODOL-1",
        plate_number="ODOL-999")
    db.session.add(FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=datetime.now(),
        litres=40, total_amount=2400, odometer_reported=65430,
        odometer_used=65430, odometer_status="OK"))
    db.session.commit()

    client = _client(app, db)
    html = client.get("/transactions/fuel").get_data(as_text=True)
    assert "65,430" in html


def test_corrected_reading_shows_both_values(app, db):
    """When a correction was applied, both the original and the
    corrected value should be visible -- not just the corrected one,
    which would hide that anything was changed at all."""
    from datetime import datetime
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelTransaction

    vt = VehicleTypeService().create(code="LV-ODOC", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-ODOC", name="Branch ODOC")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="ODOC-1",
        plate_number="ODOC-999")
    db.session.add(FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=datetime.now(),
        litres=40, total_amount=2400, odometer_reported=6543,
        odometer_used=65435, odometer_status="CORRECTED"))
    db.session.commit()

    client = _client(app, db)
    html = client.get("/transactions/fuel").get_data(as_text=True)
    assert "6,543" in html
    assert "65,435" in html


def test_date_range_filters_the_list(app, db):
    """A vehicle with two fills on different dates -- filtering to one
    date must exclude the other."""
    from datetime import datetime
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelTransaction

    vt = VehicleTypeService().create(code="LV-DR", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-DR", name="Branch DR")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="DR-1",
        plate_number="DR-JULY1")
    vehicle2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="DR-2",
        plate_number="DR-JULY5")
    db.session.add(FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=datetime(2026, 7, 1, 9, 0),
        litres=40, total_amount=2400))
    db.session.add(FuelTransaction(
        vehicle_id=vehicle2.id, transaction_date=datetime(2026, 7, 5, 9, 0),
        litres=40, total_amount=2400))
    db.session.commit()

    client = _client(app, db)
    only_july1 = client.get(
        "/transactions/fuel?date_from=2026-07-01&date_to=2026-07-01"
    ).get_data(as_text=True)
    assert "DR-JULY1" in only_july1
    assert "DR-JULY5" not in only_july1


def test_end_of_range_day_is_inclusive(app, db):
    """A transaction logged in the afternoon of the end date must still
    be included -- filtering 'to' a date must not silently mean
    midnight of that date."""
    from datetime import datetime
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelTransaction

    vt = VehicleTypeService().create(code="LV-INCL", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-INCL", name="Branch INCL")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="INCL-1",
        plate_number="INCL-PM")
    db.session.add(FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=datetime(2026, 7, 1, 15, 30),
        litres=40, total_amount=2400))
    db.session.commit()

    client = _client(app, db)
    html = client.get(
        "/transactions/fuel?date_from=2026-07-01&date_to=2026-07-01"
    ).get_data(as_text=True)
    assert "INCL-PM" in html


def test_malformed_date_does_not_crash_the_page(app, db):
    client = _client(app, db)
    r = client.get("/transactions/fuel?date_from=not-a-date&date_to=also-bad")
    assert r.status_code == 200
