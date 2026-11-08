"""FuelTransaction.needs_review -- a derived, indexed boolean kept in
sync automatically at write time.

Found during the Fuel Management performance audit (2026-11-08): the
Exceptions/flagged view (`GET /api/v1/fuel?view=flagged`) and the
Exceptions badge count (`exceptions_count()`) both filter with
`anomaly_flags IS NOT NULL OR odometer_status IN ('SUSPECT','MISSING')`
against two un-composited text/string columns. Verified with
EXPLAIN QUERY PLAN against a 1,000,000-row synthetic dataset: this OR
across two columns can't be served by either column's own index, so it
falls back to scanning every row, every time the view or badge is
hit -- and that cost only grows as fuel history accumulates,
independent of how many rows (if any) actually match.

The fix: a `needs_review` boolean column, indexed, maintained by a
single SQLAlchemy ORM event (not by every call site that happens to
set anomaly_flags or odometer_status -- that's exactly the kind of
drift this project has already been bitten by once, see the "Exceptions
badge vs Exceptions list" note in analytics.py). Querying one indexed
boolean column replaces the OR-across-two-columns scan.
"""
from datetime import datetime


def _make_vehicle(suffix):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService

    vt = VehicleTypeService().create(code=f"LV-NRC{suffix}", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code=f"BR-NRC{suffix}",
                                    name=f"Branch {suffix}")
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number=f"NRC-{suffix}")


def test_needs_review_is_false_by_default(db):
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("DEFAULT")
    txn = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                          litres=40, total_amount=2400)
    db.session.add(txn)
    db.session.commit()

    assert txn.needs_review is False


def test_needs_review_turns_true_when_anomaly_flags_is_set(db):
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("FLAG")
    txn = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                          litres=40, total_amount=2400,
                          anomaly_flags="OVER_TANK")
    db.session.add(txn)
    db.session.commit()

    assert txn.needs_review is True


def test_needs_review_turns_true_when_odometer_is_suspect_or_missing(db):
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("ODO")
    suspect = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                              litres=40, total_amount=2400,
                              odometer_status="SUSPECT")
    missing = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                              litres=40, total_amount=2400,
                              odometer_status="MISSING")
    db.session.add_all([suspect, missing])
    db.session.commit()

    assert suspect.needs_review is True
    assert missing.needs_review is True


def test_needs_review_follows_an_update_not_just_creation(db):
    """The column must stay in sync when a later process (odometer
    validation, anomaly detection) changes the underlying fields on an
    already-committed row -- not only at insert time."""
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("UPDATE")
    txn = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                          litres=40, total_amount=2400)
    db.session.add(txn)
    db.session.commit()
    assert txn.needs_review is False

    txn.odometer_status = "SUSPECT"
    db.session.commit()
    assert txn.needs_review is True

    txn.odometer_status = "OK"
    db.session.commit()
    assert txn.needs_review is False


def test_needs_review_clears_when_anomaly_flags_cleared_and_odometer_ok(db):
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("CLEAR")
    txn = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                          litres=40, total_amount=2400,
                          anomaly_flags="OVER_TANK", odometer_status="SUSPECT")
    db.session.add(txn)
    db.session.commit()
    assert txn.needs_review is True

    txn.anomaly_flags = None
    txn.odometer_status = "OK"
    db.session.commit()
    assert txn.needs_review is False


def test_exceptions_query_can_use_needs_review_alone(db):
    """The Exceptions view's filter should be expressible as a single
    indexed-boolean lookup, not an OR across two other columns."""
    from app.modules.transactions.fuel.models import FuelTransaction

    vehicle = _make_vehicle("QUERY")
    flagged = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                              litres=40, total_amount=2400,
                              anomaly_flags="OVER_TANK")
    clean = FuelTransaction(vehicle_id=vehicle.id, transaction_date=datetime.now(),
                            litres=40, total_amount=2400)
    db.session.add_all([flagged, clean])
    db.session.commit()

    matched = FuelTransaction.query.filter(
        FuelTransaction.needs_review.is_(True)).all()
    assert [t.id for t in matched] == [flagged.id]
