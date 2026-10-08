"""Edit PMS Profile (update_series) with calendar-only services.

Services were matched to the saved rows by cumulative_km. Every
calendar-only service has cumulative_km = None, so they all shared ONE
dict key: every incoming calendar-only service updated the same row (the
last one's details won) and the other saved rows were never matched --
a service removed in the editor silently stayed in the database.

Matching now, each saved row claimed at most once:
  1. by schedule id when the client sends it (rejected if the id is not
     part of this profile);
  2. otherwise by cumulative_km (km-based services -- unchanged);
  3. otherwise calendar-only services pair with saved calendar-only
     rows in their saved order.
Unmatched saved rows are deleted with their checklist, as before.
(No test exercised update_series before this file.)
"""
import pytest

from app.extensions import db as _db
from app.modules.maintenance_config.models import PMSchedule
from app.modules.maintenance_config.service import (
    InvalidScheduleError, PMScheduleService)
from app.modules.master_data.reference.service import MaintenanceTypeService


@pytest.fixture()
def mt(db):
    return MaintenanceTypeService().create(code="US-AC", name="Aircon", category="PM")


def _calendar_profile(mt, code="CAL-1"):
    PMScheduleService().generate_series(
        maintenance_type_id=mt.id, trigger_mode="CALENDAR", interval_days=180,
        profile_code=code, services=[
            {"cumulative_km": None, "work_description_template": "Aircon clean"},
            {"cumulative_km": None, "work_description_template": "Aircon regas"}])
    return _rows(code)


def _rows(code):
    _db.session.expire_all()
    return (PMSchedule.query.filter_by(profile_code=code)
            .order_by(PMSchedule.sequence_position, PMSchedule.id).all())


def _update(mt, code, services, mode="CALENDAR"):
    return PMScheduleService().update_series(
        code, maintenance_type_id=mt.id, trigger_mode=mode,
        services=services, interval_days=180)


def test_editing_two_calendar_only_services_keeps_both(db, mt):
    a, b = _calendar_profile(mt)
    _update(mt, "CAL-1", [
        {"cumulative_km": None, "work_description_template": "Clean filters"},
        {"cumulative_km": None, "work_description_template": "Regas + leak test"}])
    rows = _rows("CAL-1")
    assert [r.id for r in rows] == [a.id, b.id]           # same rows, in place
    assert [r.work_description_template for r in rows] == [
        "Clean filters", "Regas + leak test"]


def test_removing_a_calendar_only_service_really_removes_it(db, mt):
    a, b = _calendar_profile(mt)
    _update(mt, "CAL-1", [
        {"cumulative_km": None, "work_description_template": "Aircon clean"}])
    rows = _rows("CAL-1")
    assert [r.id for r in rows] == [a.id]
    assert _db.session.get(PMSchedule, b.id) is None


def test_ids_from_the_client_decide_which_row_is_which(db, mt):
    a, b = _calendar_profile(mt)
    _update(mt, "CAL-1", [
        {"id": b.id, "cumulative_km": None, "work_description_template": "B first"},
        {"id": a.id, "cumulative_km": None, "work_description_template": "A second"}])
    _db.session.expire_all()
    assert _db.session.get(PMSchedule, a.id).work_description_template == "A second"
    assert _db.session.get(PMSchedule, b.id).work_description_template == "B first"
    assert [r.id for r in _rows("CAL-1")] == [b.id, a.id]  # new order kept


def test_an_id_from_another_profile_is_refused_and_nothing_changes(db, mt):
    a, b = _calendar_profile(mt)
    (other,) = PMScheduleService().generate_series(
        maintenance_type_id=mt.id, trigger_mode="CALENDAR", interval_days=90,
        profile_code="CAL-2", services=[{"cumulative_km": None}])
    with pytest.raises(InvalidScheduleError):
        _update(mt, "CAL-1", [{"id": other.id, "cumulative_km": None}])
    _db.session.rollback()
    assert [r.id for r in _rows("CAL-1")] == [a.id, b.id]


def test_km_based_services_still_match_by_milestone(db, mt):
    PMScheduleService().generate_series(
        maintenance_type_id=mt.id, trigger_mode="KM", profile_code="KM-1",
        services=[{"cumulative_km": 5000}, {"cumulative_km": 10000}])
    k5, k10 = _rows("KM-1")
    _update(mt, "KM-1", [
        {"cumulative_km": 10000, "work_description_template": "Major"},
        {"cumulative_km": 5000, "work_description_template": "Minor"}], mode="KM")
    _db.session.expire_all()
    assert _db.session.get(PMSchedule, k5.id).work_description_template == "Minor"
    assert _db.session.get(PMSchedule, k10.id).work_description_template == "Major"
