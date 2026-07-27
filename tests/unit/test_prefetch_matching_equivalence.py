"""The schedule-matching fast path must return EXACTLY what the original
linear scan returned.

applicable_schedules() was rewritten from a scan over every active
schedule into pre-built dictionary lookups, because at production scale
(4,626 active schedules x 164 vehicles, matched again per maintenance
type) the scan measured 9.2 SECONDS to return 3 rows.

Speed is worthless if it changes which schedules a vehicle matches -- a
wrong match means the wrong PM package, on the wrong interval. These
tests pin the resolution order and the fallback chain.
"""
from datetime import date

import pytest

from app.core.maintenance.due_calculation_service import _Prefetch
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.vehicle.service import VehicleService


@pytest.fixture()
def env(db):
    vt = VehicleTypeService().create(code="LV-EQ", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="MT-EQ", name="PMS",
                                         category="PREVENTIVE")
    mt2 = MaintenanceTypeService().create(code="MT-EQ2", name="Aircon",
                                          category="PREVENTIVE")
    branch = BranchService().create(code="BR-EQ", name="Branch")
    return vt, mt, mt2, branch


def _vehicle(vt, branch, **kw):
    defaults = dict(vehicle_type_id=vt.id, brand="Mitsubishi",
                    model="Strada", year=2013, branch_id=branch.id)
    defaults.update(kw)
    return VehicleService().create(**defaults)


def test_make_model_match_wins_over_generic(db, env):
    vt, mt, mt2, branch = env
    specific = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Mitsubishi", vehicle_model="Strada")
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=9999,
        vehicle_type_id=vt.id)          # generic fallback, must NOT win

    v = _vehicle(vt, branch, conduction_number="EQ-1")
    matches = _Prefetch().applicable_schedules(v)
    assert [s.id for s in matches] == [specific.id]


def test_make_model_matching_is_case_and_space_insensitive(db, env):
    """The original lowered and stripped both sides; the bucket keys must
    preserve that or imported data with stray casing stops matching."""
    vt, mt, mt2, branch = env
    sched = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="  MITSUBISHI ", vehicle_model="strada  ")

    v = _vehicle(vt, branch, brand="Mitsubishi", model="Strada",
                 conduction_number="EQ-2")
    assert [s.id for s in _Prefetch().applicable_schedules(v)] == [sched.id]


def test_falls_back_to_vehicle_type_generic_when_no_make_model_match(
        db, env):
    vt, mt, mt2, branch = env
    generic = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=7000,
        vehicle_type_id=vt.id)

    v = _vehicle(vt, branch, brand="Nomatch", model="Nomatch",
                 conduction_number="EQ-3")
    assert [s.id for s in _Prefetch().applicable_schedules(v)] == [generic.id]


def test_falls_back_to_global_generic_last(db, env):
    vt, mt, mt2, branch = env
    glob = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=8000)

    v = _vehicle(vt, branch, brand="Nomatch", model="Nomatch",
                 conduction_number="EQ-4")
    assert [s.id for s in _Prefetch().applicable_schedules(v)] == [glob.id]


def test_explicitly_assigned_schedule_overrides_everything(db, env):
    vt, mt, mt2, branch = env
    assigned = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1234)
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Mitsubishi", vehicle_model="Strada")

    v = _vehicle(vt, branch, conduction_number="EQ-5")
    v.pm_schedule_id = assigned.id
    db.session.commit()
    assert [s.id for s in _Prefetch().applicable_schedules(v)] == [assigned.id]


def test_maintenance_type_filter_applies_within_the_matched_bucket(db, env):
    """Filtering by type must narrow the matched set, and must fall
    through the chain when the matched bucket has nothing of that type --
    the same as filtering the full list up front."""
    vt, mt, mt2, branch = env
    pms = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Mitsubishi", vehicle_model="Strada")
    aircon = PMScheduleService().create(
        maintenance_type_id=mt2.id, trigger_mode="KM", interval_km=20000,
        vehicle_make="Mitsubishi", vehicle_model="Strada")

    v = _vehicle(vt, branch, conduction_number="EQ-6")
    prefetch = _Prefetch()
    assert [s.id for s in prefetch.applicable_schedules(v, mt.id)] == [pms.id]
    assert [s.id for s in prefetch.applicable_schedules(v, mt2.id)] == [aircon.id]


def test_repeated_calls_return_the_same_result(db, env):
    """The memo must not corrupt or reorder results between calls."""
    vt, mt, mt2, branch = env
    PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        vehicle_make="Mitsubishi", vehicle_model="Strada")
    v = _vehicle(vt, branch, conduction_number="EQ-7")

    prefetch = _Prefetch()
    first = [s.id for s in prefetch.applicable_schedules(v)]
    second = [s.id for s in prefetch.applicable_schedules(v)]
    assert first == second and first
