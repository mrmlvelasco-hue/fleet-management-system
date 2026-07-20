"""Tests for the LTO plate-based renewal schedule engine and the
Vehicle Registration validation rules added alongside it:
  - get_plate_schedule() / calculate_due_date_from_plate() /
    next_due_date_from_plate() (pure functions, no DB)
  - RegistrationDueCalculationService.get_due_status() cross-checking a
    COMPLETED registration's expiry_date against the plate schedule
  - VehicleRegistrationService.complete() rejecting registration_date >
    expiry_date, duplicate OR numbers, and duplicate CR numbers
"""
from datetime import date

import pytest

from app.modules.registration_config.lto_plate_schedule import (
    get_plate_schedule, calculate_due_date_from_plate,
    next_due_date_from_plate)
from app.modules.registration_config.service import (
    RegistrationDueCalculationService)
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService, RegistrationDateOrderError,
    DuplicateORNumberError, DuplicateCRNumberError)
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.extensions import db


# ── Pure plate-schedule math (no DB needed) ─────────────────────────────────

def test_get_plate_schedule_matches_spec_example():
    # Last digit 7 -> July; second-to-last digit 8 -> Week 3
    sched = get_plate_schedule("AKA-1187")
    assert sched == {"last_digit": 7, "month": 7, "month_name": "July",
                     "week": 3}


def test_calculate_due_date_from_plate_matches_spec_example():
    assert calculate_due_date_from_plate("AKA-1187", 2026) == date(2026, 7, 21)


@pytest.mark.parametrize("last_digit,expected_month", [
    (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
    (7, 7), (8, 8), (9, 9), (0, 10),
])
def test_month_mapping_for_every_last_digit(last_digit, expected_month):
    sched = get_plate_schedule(f"ABC-1{last_digit}")
    assert sched["month"] == expected_month


@pytest.mark.parametrize("second_last_digit,expected_week", [
    (1, 1), (2, 1), (3, 1),
    (4, 2), (5, 2), (6, 2),
    (7, 3), (8, 3),
    (9, 4), (0, 4),
])
def test_week_mapping_for_every_second_last_digit(second_last_digit, expected_week):
    sched = get_plate_schedule(f"ABC-{second_last_digit}1")
    assert sched["week"] == expected_week


def test_get_plate_schedule_returns_none_for_plate_with_no_digits():
    assert get_plate_schedule("NOPLATE") is None
    assert get_plate_schedule(None) is None
    assert get_plate_schedule("") is None


def test_calculate_due_date_caps_to_days_in_month():
    # Week 4 -> day 28, but February in a non-leap year only has 28 days
    # anyway; a 30/31-day month's week-4 day should still be exactly 28,
    # per the spec's day = min(week*7, days_in_month) rule.
    due = calculate_due_date_from_plate("ABC-19", 2026)  # last=9->Sep, 2nd-last=1->week1
    assert due == date(2026, 9, 7)


def test_next_due_date_from_plate_rolls_to_next_year_if_passed():
    # As-of date is after this year's schedule date -> should roll to
    # next year's occurrence instead of returning a date in the past.
    result = next_due_date_from_plate("AKA-1187", date(2026, 12, 1))
    assert result == date(2027, 7, 21)


def test_next_due_date_from_plate_keeps_this_year_if_not_yet_passed():
    result = next_due_date_from_plate("AKA-1187", date(2026, 1, 1))
    assert result == date(2026, 7, 21)


# ── Due-status integration (DB-backed) ──────────────────────────────────────

@pytest.fixture()
def vehicle(db):
    branch = BranchService().create(code="BR-LTO", name="LTO Branch")
    vt = VehicleTypeService().create(code="LV-LTO", name="Light", category="LIGHT")
    return VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2026,
        branch_id=branch.id, conduction_number="LTO-000",
        plate_number="AKA-1187")


def test_get_due_status_no_record_falls_back_to_plate_schedule(vehicle):
    result = RegistrationDueCalculationService().get_due_status(
        vehicle, as_of_date=date(2026, 1, 1))
    assert result["status"] == "NO_RECORD"
    assert result["source"] == "PLATE_SCHEDULE"
    assert result["lto_month"] == 7
    assert result["lto_week"] == 3
    assert result["suggested_due_date"] == date(2026, 7, 21)


def test_get_due_status_uses_registration_record_as_source_of_truth(
        db, vehicle):
    reg = VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="COMPLETED",
        registration_date=date(2026, 6, 1), expiry_date=date(2026, 7, 21),
        or_number="OR-LTO-1", cr_number="CR-LTO-1")
    db.session.add(reg)
    db.session.commit()

    result = RegistrationDueCalculationService().get_due_status(
        vehicle, as_of_date=date(2026, 7, 10))
    assert result["source"] == "REGISTRATION_RECORD"
    assert result["stored_expiry_date"] == date(2026, 7, 21)
    assert result["calculated_due_date"] == date(2026, 7, 21)
    assert result["warning"] is None
    assert result["status"] == "DUE_SOON"


def test_get_due_status_flags_mismatch_when_expiry_differs_from_schedule(
        db, vehicle):
    reg = VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="COMPLETED",
        registration_date=date(2026, 6, 1), expiry_date=date(2026, 9, 1),
        or_number="OR-LTO-2", cr_number="CR-LTO-2")
    db.session.add(reg)
    db.session.commit()

    result = RegistrationDueCalculationService().get_due_status(
        vehicle, as_of_date=date(2026, 7, 10))
    assert result["warning"] == "REGISTRATION_DATE_MISMATCH"
    assert result["calculated_due_date"] == date(2026, 7, 21)
    assert result["stored_expiry_date"] == date(2026, 9, 1)


def test_get_all_due_vehicles_statuses_param_is_backward_compatible(
        db, vehicle):
    """Default call (no `statuses`) must behave exactly as before this
    change: only DUE_SOON/OVERDUE, so existing callers (dashboard widget,
    auto-generation task) aren't affected by the new NO_RECORD/GOOD
    reporting capability."""
    # This vehicle is NO_RECORD -- must NOT appear in the default call.
    default_rows = RegistrationDueCalculationService().get_all_due_vehicles()
    assert vehicle.id not in [r["vehicle"].id for r in default_rows]

    # But must appear when NO_RECORD is explicitly requested.
    explicit_rows = RegistrationDueCalculationService().get_all_due_vehicles(
        statuses=("NO_RECORD",))
    assert vehicle.id in [r["vehicle"].id for r in explicit_rows]


# ── Registration completion validation ──────────────────────────────────────

@pytest.fixture()
def draft_registration(db, vehicle):
    return VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="DRAFT",
        registration_date=date(2026, 1, 1), expiry_date=date(2027, 1, 1))


def test_complete_rejects_registration_date_after_expiry_date(db, vehicle):
    reg = VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="DRAFT",
        registration_date=date(2026, 8, 1), expiry_date=date(2026, 7, 1))
    db.session.add(reg)
    db.session.commit()
    with pytest.raises(RegistrationDateOrderError):
        VehicleRegistrationService().complete(
            reg.id, or_number="OR-BAD", cr_number="CR-BAD")


def test_complete_rejects_duplicate_or_number(db, vehicle, draft_registration):
    existing = VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="COMPLETED",
        registration_date=date(2025, 1, 1), expiry_date=date(2026, 1, 1),
        or_number="OR-DUP", cr_number="CR-UNIQUE-A")
    db.session.add_all([existing, draft_registration])
    db.session.commit()

    with pytest.raises(DuplicateORNumberError):
        VehicleRegistrationService().complete(
            draft_registration.id, or_number="OR-DUP", cr_number="CR-UNIQUE-B")


def test_complete_rejects_duplicate_cr_number(db, vehicle, draft_registration):
    existing = VehicleRegistration(
        vehicle_id=vehicle.id, registration_type="RENEWAL", status="COMPLETED",
        registration_date=date(2025, 1, 1), expiry_date=date(2026, 1, 1),
        or_number="OR-UNIQUE-A", cr_number="CR-DUP")
    db.session.add_all([existing, draft_registration])
    db.session.commit()

    with pytest.raises(DuplicateCRNumberError):
        VehicleRegistrationService().complete(
            draft_registration.id, or_number="OR-UNIQUE-B", cr_number="CR-DUP")


def test_complete_succeeds_with_valid_unique_data(db, draft_registration):
    db.session.add(draft_registration)
    db.session.commit()
    result = VehicleRegistrationService().complete(
        draft_registration.id, or_number="OR-VALID", cr_number="CR-VALID")
    assert result.status == "COMPLETED"
    assert result.or_number == "OR-VALID"
