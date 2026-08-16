"""Selectable date column on the standard transaction filter.

Each transaction type records its own natural date under a different
name -- a Trip Ticket departs, an ATD is valid from, a Vehicle Movement
happens on a date -- and until now the shared filter guessed which
column to use from an ordered candidate list. For three modules that
guess fell all the way through to `created_at`, so the filter silently
ranged and sorted on when the row was typed rather than on the date
displayed in the column beside it. Nothing looked broken; the numbers
just wouldn't tie out against a report.

The fix is to stop guessing: every service declares the date columns it
can be filtered on, and the person picks which one the range applies to.
"""
import pytest
from datetime import date, datetime, timedelta

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


# ── Every module declares its own date columns ──────────────────────

@pytest.mark.parametrize("import_path, service_name, expected_default", [
    ("app.modules.transactions.trip_ticket.service",
     "TripTicketService", "departure_datetime"),
    ("app.modules.transactions.atd.service",
     "ATDService", "valid_from"),
    ("app.modules.transactions.vehicle_movement.service",
     "VehicleMovementService", "movement_date"),
    ("app.modules.transactions.maintenance_order.service",
     "MaintenanceOrderService", "scheduled_date"),
    ("app.modules.transactions.tire_txn.service",
     "TireTransactionService", "transaction_date"),
    ("app.modules.transactions.battery_txn.service",
     "BatteryTransactionService", "transaction_date"),
    ("app.modules.transactions.vehicle_registration.service",
     "VehicleRegistrationService", "registration_date"),
])
def test_each_service_declares_its_real_business_date(
        app, db, import_path, service_name, expected_default):
    """The DEFAULT date column must be the one the list actually shows,
    not whatever an ordered candidate list happens to land on."""
    import importlib
    svc = getattr(importlib.import_module(import_path), service_name)()
    assert svc.default_date_field() == expected_default


@pytest.mark.parametrize("import_path, service_name", [
    ("app.modules.transactions.trip_ticket.service", "TripTicketService"),
    ("app.modules.transactions.atd.service", "ATDService"),
    ("app.modules.transactions.vehicle_movement.service",
     "VehicleMovementService"),
])
def test_created_date_is_offered_everywhere(app, db, import_path,
                                            service_name):
    """These three display no created date of their own, so it has to be
    reachable through the filter or there is no way to ask "what was
    entered last week"."""
    import importlib
    svc = getattr(importlib.import_module(import_path), service_name)()
    codes = [c for c, _ in svc.date_field_choices()]
    assert "created_at" in codes


def test_date_field_choices_are_labelled_for_the_dropdown(app, db):
    from app.modules.transactions.trip_ticket.service import TripTicketService
    choices = TripTicketService().date_field_choices()
    assert ("departure_datetime", "Departure Date") in choices
    assert ("created_at", "Created Date") in choices


def test_declared_date_fields_all_exist_on_the_model(app, db):
    """A typo in a declaration would otherwise fall back silently."""
    import importlib
    for path, name in [
            ("app.modules.transactions.trip_ticket.service",
             "TripTicketService"),
            ("app.modules.transactions.atd.service", "ATDService"),
            ("app.modules.transactions.vehicle_movement.service",
             "VehicleMovementService"),
            ("app.modules.transactions.maintenance_order.service",
             "MaintenanceOrderService")]:
        svc = getattr(importlib.import_module(path), name)()
        for code, _label in svc.date_field_choices():
            assert getattr(svc.model, code, None) is not None, (
                f"{name} declares {code}, which {svc.model.__name__} "
                f"does not have")


# ── Choosing which column the range applies to ──────────────────────

def _make_tt(db, admin, vehicle, departure, created):
    from app.modules.transactions.trip_ticket.models import TripTicket
    tt = TripTicket(vehicle_id=vehicle.id, driver_name_manual="D",
                    destination="X", purpose="P",
                    departure_datetime=departure, status="DRAFT",
                    requested_by=admin.id)
    db.session.add(tt)
    db.session.flush()
    # created_at is set by the base model default; overridden here so the
    # two dates are deliberately far apart.
    tt.created_at = created
    db.session.commit()
    return tt


@pytest.fixture()
def vehicle(app, db):
    """Built through the services rather than raw models so the required
    columns come from the same place the app fills them."""
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService
    vt = VehicleTypeService().create(code="VAN-DF", name="Van",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-DF", name="Branch")
    return VehicleService().create(
        vehicle_type_id=vt.id, branch_id=branch.id, brand="Toyota",
        model="Hiace", year=2020, plate_number="ABC-1234",
        conduction_number="CN-DF1")


def test_filtering_on_the_business_date_by_default(app, db, admin, vehicle):
    from app.modules.transactions.trip_ticket.service import TripTicketService
    # Departs in March, was typed in January.
    _make_tt(db, admin, vehicle, datetime(2026, 3, 10, 8, 0),
             datetime(2026, 1, 5, 9, 0))
    rows, _ = TripTicketService().list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert len(rows) == 1


def test_switching_to_created_date_changes_what_matches(
        app, db, admin, vehicle):
    """The whole point of the selector: the same range against a
    different column returns a different answer."""
    from app.modules.transactions.trip_ticket.service import TripTicketService
    _make_tt(db, admin, vehicle, datetime(2026, 3, 10, 8, 0),
             datetime(2026, 1, 5, 9, 0))
    svc = TripTicketService()

    march_by_departure, _ = svc.list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31),
        date_field="departure_datetime")
    march_by_created, _ = svc.list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31),
        date_field="created_at")
    january_by_created, _ = svc.list_filtered(
        date_from=date(2026, 1, 1), date_to=date(2026, 1, 31),
        date_field="created_at")

    assert len(march_by_departure) == 1
    assert len(march_by_created) == 0
    assert len(january_by_created) == 1


def test_an_unknown_date_field_falls_back_rather_than_raising(
        app, db, admin, vehicle):
    """The field arrives from a query string, so anyone can type
    anything into it."""
    from app.modules.transactions.trip_ticket.service import TripTicketService
    _make_tt(db, admin, vehicle, datetime(2026, 3, 10, 8, 0),
             datetime(2026, 1, 5, 9, 0))
    rows, _ = TripTicketService().list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31),
        date_field="'; DROP TABLE trip_tickets; --")
    assert len(rows) == 1        # fell back to the default column


def test_results_are_sorted_by_the_chosen_column(app, db, admin, vehicle):
    from app.modules.transactions.trip_ticket.service import TripTicketService
    # A departs later but was created earlier.
    a = _make_tt(db, admin, vehicle, datetime(2026, 5, 1, 8, 0),
                 datetime(2026, 1, 1, 9, 0))
    b = _make_tt(db, admin, vehicle, datetime(2026, 4, 1, 8, 0),
                 datetime(2026, 2, 1, 9, 0))
    svc = TripTicketService()
    by_departure, _ = svc.list_filtered(date_field="departure_datetime")
    by_created, _ = svc.list_filtered(date_field="created_at")
    assert [r.id for r in by_departure] == [a.id, b.id]
    assert [r.id for r in by_created] == [b.id, a.id]


# ── The end of the range must actually include the end day ──────────

def test_end_of_range_includes_the_whole_final_day(app, db, admin, vehicle):
    """`col <= date_to` compares a DateTime against MIDNIGHT of that
    day, so a trip departing at 08:00 on the 31st was excluded from a
    range ending on the 31st. Latent until now only because the one
    converted list (Maintenance Orders) filters on a plain Date column;
    every column these eight lists use is a DateTime.
    """
    from app.modules.transactions.trip_ticket.service import TripTicketService
    _make_tt(db, admin, vehicle, datetime(2026, 3, 31, 8, 0),
             datetime(2026, 1, 5, 9, 0))
    rows, _ = TripTicketService().list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert len(rows) == 1, (
        "a document dated on the final day of the range was excluded")


def test_end_of_range_still_excludes_the_following_day(
        app, db, admin, vehicle):
    """The inclusivity fix must not quietly widen the range by a day."""
    from app.modules.transactions.trip_ticket.service import TripTicketService
    _make_tt(db, admin, vehicle, datetime(2026, 4, 1, 0, 30),
             datetime(2026, 1, 5, 9, 0))
    rows, _ = TripTicketService().list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert len(rows) == 0


def test_inclusivity_holds_for_plain_date_columns_too(app, db, admin, vehicle):
    """Maintenance Order filters on a Date, not a DateTime -- the fix
    must not regress the one module that already worked."""
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    mo = MaintenanceOrder(scheduled_date=date(2026, 3, 31), status="DRAFT",
                          vehicle_id=vehicle.id, requested_by=admin.id,
                          order_category="MAINTENANCE")
    db.session.add(mo)
    db.session.commit()
    rows, _ = MaintenanceOrderService().list_filtered(
        date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert len(rows) == 1
