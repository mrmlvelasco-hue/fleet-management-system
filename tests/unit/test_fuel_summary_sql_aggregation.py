"""FuelAnalyticsService.summary() must aggregate in SQL, not Python.

Found during the Fuel Management performance audit (2026-11-08): at
5,000 vehicles / ~1,000,000 fuel-transaction scale, every single call
to GET /api/v1/fuel -- regardless of page, filter, or how few rows the
visible list actually shows -- paid ~2.5-3 seconds, almost entirely
inside FuelAnalyticsService.summary(). The reason: summary() pulled
every matching row into Python as full ORM objects via `.all()` and
then summed litres/spend/efficiency in a loop, instead of asking the
database to SUM()/COUNT()/AVG(). The cost scaled with the number of
rows IN THE WINDOW, not with the page size -- so it got worse as the
fleet's fuel history grew, with no way for a caller to avoid it.

This file pins the fix at the query-count level (the number of SQL
statements summary() issues must not grow with the row count) while
every test in test_fuel_import_robustness.py pins the unchanged
business contract (the 30-day window, the all-time fallback, the
needs_review de-duplication, company/branch scoping).
"""
from datetime import datetime, timedelta

from sqlalchemy import event


def _count_hydrated_rows(fn, *a, **kw):
    """How many FuelTransaction ORM instances get hydrated while fn runs.

    This is the number that actually distinguishes "SQL does the
    arithmetic" from "Python loads every row and sums it in a loop":
    SQL statement count stays at 1 either way (`.all()` is one
    `cursor.execute()` regardless of row count), but a `.load()`
    mapper event fires once per ORM object actually constructed, which
    is exactly the cost a `.all()`-and-sum-in-Python approach pays per
    matched row and a SQL SUM()/COUNT() avoids entirely.
    """
    from app.modules.transactions.fuel.models import FuelTransaction

    count = [0]

    def _listener(target, context):
        count[0] += 1

    event.listen(FuelTransaction, "load", _listener)
    try:
        result = fn(*a, **kw)
    finally:
        event.remove(FuelTransaction, "load", _listener)
    return result, count[0]


def _make_vehicle(db, suffix):
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService

    vt = VehicleTypeService().create(code=f"LV-SQLAGG{suffix}", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code=f"BR-SQLAGG{suffix}",
                                    name=f"Branch {suffix}")
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number=f"SQLAGG-{suffix}")


def test_summary_does_not_hydrate_a_fueltransaction_object_per_row(db):
    """The real regression: summary() must never construct one ORM
    FuelTransaction instance per matched row. A SQL SUM()/COUNT()/AVG()
    aggregate hydrates ZERO FuelTransaction objects no matter how many
    rows match; a `.all()`-then-sum-in-Python approach hydrates exactly
    one per matched row, which is the entire cost this fix removes."""
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    vehicle = _make_vehicle(db, "GROW")
    now = datetime.now()
    rows = [FuelTransaction(vehicle_id=vehicle.id,
                            transaction_date=now - timedelta(days=1, hours=i),
                            litres=40, total_amount=2400,
                            km_per_litre=10 + (i % 3))
            for i in range(300)]
    db.session.add_all(rows)
    db.session.commit()
    # Simulate a fresh request: nothing already sitting in the
    # session's identity map, which is what masked this on the first
    # pass -- SQLAlchemy skips the 'load' event for rows it can serve
    # straight out of the identity map instead of re-hydrating them.
    db.session.expunge_all()

    _, hydrated = _count_hydrated_rows(FuelAnalyticsService().summary)

    assert hydrated == 0


def test_summary_figures_match_sql_aggregation_not_python_loop(db):
    """Correctness check alongside the query-count check: the actual
    numbers must still be right once computed in SQL."""
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    vehicle = _make_vehicle(db, "CORRECT")
    now = datetime.now()
    db.session.add_all([
        FuelTransaction(vehicle_id=vehicle.id, transaction_date=now - timedelta(hours=1),
                        litres=40, total_amount=2400, km_per_litre=10),
        FuelTransaction(vehicle_id=vehicle.id, transaction_date=now - timedelta(hours=2),
                        litres=30, total_amount=1800, km_per_litre=12,
                        anomaly_flags="OVER_TANK"),
        FuelTransaction(vehicle_id=vehicle.id, transaction_date=now - timedelta(hours=3),
                        litres=20, total_amount=1200, odometer_status="SUSPECT"),
    ])
    db.session.commit()

    summary = FuelAnalyticsService().summary()
    assert summary["fills"] == 3
    assert summary["litres"] == 90
    assert summary["spend"] == 5400
    assert summary["avg_price"] == round(5400 / 90, 2)
    assert summary["avg_kmpl"] == 11       # (10+12)/2, 0-litre-only rows excluded is n/a here
    assert summary["flagged"] == 1
    assert summary["untrusted_odometer"] == 1
    assert summary["needs_review"] == 2
    assert summary["is_all_time"] is False
