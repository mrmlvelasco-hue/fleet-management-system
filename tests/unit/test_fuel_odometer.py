"""Odometer validation for fleet-card fuel data.

The client's Petron fleet card captures an odometer typed by the driver
at the pump, and a meaningful share are wrong. This matters more than it
first appears: consumption is the distance BETWEEN fills, so one bad
reading corrupts two fills, and a handful of typos makes fleet-wide
efficiency reporting untrustworthy -- which is worse than having none,
because people act on it.

Principle under test: never discard, never silently fix. The money was
really spent so the transaction always stands; only the CONSUMPTION is
quarantined, always with a stated reason.
"""
from datetime import datetime, timedelta

import pytest

from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.fuel.models import FuelTransaction
from app.modules.transactions.fuel.odometer_validation import (
    OdometerValidationService)

BASE = datetime(2026, 1, 1, 8, 0)


@pytest.fixture()
def vehicle(db):
    vt = VehicleTypeService().create(code="LV-FU", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-FU", name="Branch")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="FU-1")
    db.session.commit()
    return v


def _fill(db, vehicle, days, odometer, litres=50, amount=3000):
    txn = FuelTransaction(
        vehicle_id=vehicle.id, transaction_date=BASE + timedelta(days=days),
        litres=litres, total_amount=amount, odometer_reported=odometer)
    db.session.add(txn)
    db.session.flush()
    OdometerValidationService().validate(txn)
    return txn


def test_first_fill_is_a_baseline_not_a_consumption_figure(db, vehicle):
    """Nothing to measure against yet -- a real limitation, stated
    rather than papered over with a fabricated number."""
    txn = _fill(db, vehicle, 0, 65000)
    assert txn.odometer_status == "OK"
    assert txn.odometer_used == 65000
    assert txn.km_per_litre is None
    assert "baseline" in txn.odometer_note


def test_normal_fill_computes_consumption(db, vehicle):
    _fill(db, vehicle, 0, 65000)
    txn = _fill(db, vehicle, 7, 65430, litres=50)
    assert txn.odometer_status == "OK"
    assert txn.distance_km == 430
    assert float(txn.km_per_litre) == pytest.approx(8.6, abs=0.01)


def test_dropped_final_digit_is_corrected(db, vehicle):
    """65,430 typed as 6,543. Every candidate clusters within 8 km, so
    the hypothesis is unambiguous even though there are nine of them."""
    _fill(db, vehicle, 0, 65000)
    _fill(db, vehicle, 7, 65430)
    txn = _fill(db, vehicle, 14, 6543)
    assert txn.odometer_status == "CORRECTED"
    assert 65431 <= txn.odometer_used <= 65439
    assert "mistyped digit" in txn.odometer_note


def test_litres_typed_into_the_odometer_box_is_quarantined(db, vehicle):
    _fill(db, vehicle, 0, 66200)
    txn = _fill(db, vehicle, 7, 45)
    assert txn.odometer_status == "SUSPECT"
    assert txn.odometer_used is None
    assert txn.km_per_litre is None


def test_a_suspect_reading_still_records_the_money(db, vehicle):
    """The fuel was genuinely bought. Only the consumption is in doubt,
    and losing the financial record to protect a statistic would be the
    wrong trade."""
    _fill(db, vehicle, 0, 66200)
    txn = _fill(db, vehicle, 7, 45, litres=52, amount=3120)
    assert txn.odometer_status == "SUSPECT"
    assert float(txn.litres) == 52
    assert float(txn.total_amount) == 3120


def test_implausible_jump_is_flagged_not_silently_accepted(db, vehicle):
    _fill(db, vehicle, 0, 65000)
    txn = _fill(db, vehicle, 7, 400000)
    assert txn.odometer_status in ("SUSPECT", "CORRECTED")
    if txn.odometer_status == "SUSPECT":
        assert txn.odometer_used is None


def test_missing_reading_is_distinguished_from_a_wrong_one(db, vehicle):
    """MISSING and SUSPECT need different follow-up: one is chased with
    the driver, the other is corrected in the record."""
    _fill(db, vehicle, 0, 65000)
    txn = _fill(db, vehicle, 7, None)
    assert txn.odometer_status == "MISSING"
    assert txn.odometer_used is None


def test_a_bad_reading_does_not_corrupt_the_following_fill(db, vehicle):
    """The whole point. A suspect reading is skipped as a baseline, so
    the next good fill measures from the last TRUSTWORTHY one instead of
    inheriting the error."""
    _fill(db, vehicle, 0, 65000)
    _fill(db, vehicle, 7, 65430)
    _fill(db, vehicle, 14, 45)            # junk
    good = _fill(db, vehicle, 21, 66200)
    assert good.odometer_status == "OK"
    assert good.distance_km == 770        # from 65,430, not from 45


def test_accepting_a_reading_repairs_everything_downstream(db, vehicle):
    """A genuinely long trip, confirmed by a person. Fills after it must
    be recomputed, not left wrong."""
    _fill(db, vehicle, 0, 65000)
    long_trip = _fill(db, vehicle, 7, 70000)
    later = _fill(db, vehicle, 14, 70400)
    assert long_trip.odometer_status == "SUSPECT"

    OdometerValidationService().accept_reported(long_trip)
    db.session.expire_all()

    refreshed = db.session.get(FuelTransaction, later.id)
    assert refreshed.odometer_status == "OK"
    assert refreshed.distance_km == 400


def test_revalidating_a_vehicle_is_idempotent(db, vehicle):
    _fill(db, vehicle, 0, 65000)
    _fill(db, vehicle, 7, 65430)
    svc = OdometerValidationService()
    svc.revalidate_vehicle(vehicle.id)
    first = [(t.odometer_status, t.distance_km)
            for t in FuelTransaction.query.order_by(
                FuelTransaction.transaction_date).all()]
    svc.revalidate_vehicle(vehicle.id)
    second = [(t.odometer_status, t.distance_km)
             for t in FuelTransaction.query.order_by(
                 FuelTransaction.transaction_date).all()]
    assert first == second


def test_a_plausible_transposition_is_honestly_undetectable(db, vehicle):
    """65,430 typed as 65,340 still looks like a normal 340 km trip.
    Documented deliberately: claiming to catch every typo would be a
    false promise, and this one is indistinguishable from real travel."""
    _fill(db, vehicle, 0, 65000)
    txn = _fill(db, vehicle, 7, 65340)
    assert txn.odometer_status == "OK"


def test_confirmation_survives_a_later_revalidation(db, vehicle):
    """The bug this flag exists to prevent: correcting an EARLY fill
    re-runs validation across the whole vehicle, which previously
    overturned every human decision made after it."""
    _fill(db, vehicle, 0, 65000)
    long_trip = _fill(db, vehicle, 7, 70000)
    svc = OdometerValidationService()
    svc.accept_reported(long_trip)

    svc.revalidate_vehicle(vehicle.id)
    db.session.expire_all()

    refreshed = db.session.get(FuelTransaction, long_trip.id)
    assert refreshed.odometer_confirmed is True
    assert refreshed.odometer_status == "CORRECTED"
    assert refreshed.odometer_used == 70000
