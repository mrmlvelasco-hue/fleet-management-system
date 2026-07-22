"""Tests for PMPackageRecommendationService -- the sequenced-package
"whichever comes first" recommendation that auto-selects the next PM
package on the Maintenance Order form.

Covers the two behaviors the user explicitly specified:
- next package = the one AFTER the last completed package in sequence
  order, with due = last service + that package's own interval;
- a high-odometer vehicle with NO logged history is measured FORWARD
  from where it is now (next package only), NOT flagged with a whole
  backlog of every interval it ever passed.
"""
from datetime import date, timedelta

import pytest

from app.core.maintenance.pm_package_recommendation_service import (
    PMPackageRecommendationService)
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)


@pytest.fixture()
def profile(db):
    """A 3-package PMS Profile: first service at 1,000km/30d, then a
    package recurring every 5,000km/90d (two rows of it, as the real
    pre-expanded data has)."""
    vt = VehicleTypeService().create(code="LV-REC", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-REC", name="PMS",
                                         category="PREVENTIVE")
    p1 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="HYBRID", interval_km=1000,
        interval_days=30, profile_code="REC-PROF", sequence_position=1)
    p2 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="HYBRID", interval_km=5000,
        interval_days=90, profile_code="REC-PROF", sequence_position=2)
    p3 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="HYBRID", interval_km=5000,
        interval_days=90, profile_code="REC-PROF", sequence_position=3)
    return vt, mt, [p1, p2, p3]


@pytest.fixture()
def vehicle(db, profile):
    vt, mt, packages = profile
    branch = BranchService().create(code="BR-REC", name="Rec Branch")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Ford", model="Escape", year=2020,
        branch_id=branch.id, conduction_number="REC-000")
    v.pm_schedule_id = packages[0].id
    from app.extensions import db as _db
    _db.session.commit()
    return v


def test_new_vehicle_recommends_first_package(db, vehicle, profile):
    vt, mt, packages = profile
    vehicle.current_odometer = 900
    from app.extensions import db as _db
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["recommended_package"].sequence_position == 1
    assert rec["due_odometer"] == 1000  # first-service MILESTONE at 1,000 km
    assert rec["status"] in ("UPCOMING", "DUE")
    assert rec["due_by"] == "BOTH"


def test_high_odometer_no_history_uses_recurring_package_next_milestone(
        db, vehicle, profile):
    """The user's actual scenario: an existing 60,000 km fleet vehicle
    entered with no logged PM history must be recommended the RECURRING
    package's NEXT MILESTONE (65,000 km on a 5,000 km interval) -- not
    its long-past 'first 1,000 km' service, and not a backlog."""
    vehicle.current_odometer = 60000
    from app.extensions import db as _db
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["recommended_package"].interval_km == 5000  # recurring, not first
    assert rec["due_odometer"] == 65000  # next 5,000 milestone above 60,000
    assert rec["status"] == "UPCOMING"


def test_64999_km_is_due_at_the_65000_milestone(db, vehicle, profile):
    """The exact example the user gave: at 64,999 km on a 5,000 km
    interval, the next PM is the 65,000 km milestone, and it's DUE
    (within the notify window) -- so a fleet person can create the PM in
    advance."""
    vehicle.current_odometer = 64999
    from app.extensions import db as _db
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["due_odometer"] == 65000
    assert rec["status"] == "DUE"


def test_after_completing_first_package_recommends_second(
        db, vehicle, profile):
    vt, mt, packages = profile
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    pm_schedule_id=packages[0].id,
                    scheduled_date=date.today() - timedelta(days=10),
                    user=None)
    mo.status = "COMPLETED"
    mo.completed_date = date.today() - timedelta(days=10)
    mo.odometer_at_service = 1000
    from app.extensions import db as _db
    _db.session.commit()

    vehicle.current_odometer = 4200
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["recommended_package"].sequence_position == 2
    assert rec["due_odometer"] == 5000  # next 5,000 milestone


def test_km_overdue_flagged(db, vehicle, profile):
    vt, mt, packages = profile
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    pm_schedule_id=packages[0].id,
                    scheduled_date=date.today(), user=None)
    mo.status = "COMPLETED"
    mo.completed_date = date.today()
    mo.odometer_at_service = 5000
    from app.extensions import db as _db
    _db.session.commit()

    vehicle.current_odometer = 10100  # past the 10,000 milestone
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["status"] == "OVERDUE"


def test_date_overdue_flagged_even_when_km_not_due(db, vehicle, profile):
    """Whichever-comes-first: overdue by DATE must flag even when the
    odometer hasn't reached the KM threshold."""
    vt, mt, packages = profile
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    pm_schedule_id=packages[0].id,
                    scheduled_date=date.today() - timedelta(days=120),
                    user=None)
    mo.status = "COMPLETED"
    mo.completed_date = date.today() - timedelta(days=120)  # > 90-day interval
    mo.odometer_at_service = 1000
    from app.extensions import db as _db
    _db.session.commit()

    vehicle.current_odometer = 3000  # under the 5,000 km milestone
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["status"] == "OVERDUE"
    assert "date" in rec["reason"].lower()


def test_no_schedule_returns_good_with_reason(db):
    branch = BranchService().create(code="BR-NOSCHED", name="No Sched")
    vt = VehicleTypeService().create(code="LV-NOSCHED", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Nomatch", model="Nomatch", year=2020,
        branch_id=branch.id, conduction_number="NOSCHED-0")
    rec = PMPackageRecommendationService().recommend(v)
    assert rec["recommended_package"] is None
    assert rec["status"] == "GOOD"
    assert rec["reason"]


@pytest.fixture()
def milestone_profile(db):
    """A realistic milestone profile mirroring the real Strada data: a
    first-service package, then packages at cumulative 5,000 / 10,000 /
    ... / 65,000 / 70,000 km. Every package has interval_km=5,000 (the
    constant recurring step) but a DISTINCT cumulative_km -- which is the
    field that actually identifies the package for a given odometer."""
    vt = VehicleTypeService().create(code="LV-MILE", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="PMS-MILE", name="PMS",
                                         category="PREVENTIVE")
    branch = BranchService().create(code="BR-MILE", name="Mile Branch")
    pkgs = []
    # seq 1 = first 1,000 km, then 5,000-step milestones through 70,000
    p1 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="HYBRID", interval_km=1000,
        interval_days=30, profile_code="MILE-PROF", sequence_position=1,
        cumulative_km=1000)
    pkgs.append(p1)
    for i, milestone in enumerate(range(5000, 70001, 5000), start=2):
        pkgs.append(PMScheduleService().create(
            maintenance_type_id=mt.id, trigger_mode="HYBRID",
            interval_km=5000, interval_days=90, profile_code="MILE-PROF",
            sequence_position=i, cumulative_km=milestone))
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Mitsubishi", model="Strada", year=2013,
        branch_id=branch.id, conduction_number="WIE231")
    v.pm_schedule_id = p1.id
    from app.extensions import db as _db
    _db.session.commit()
    return vt, mt, pkgs, v


def test_milestone_64999_selects_the_65000_package_not_the_last(
        db, milestone_profile):
    """The reported bug: a 65,000 km Strada was auto-selecting Package 22
    (the last in the list) instead of the 65,000-km package. With
    cumulative_km milestones, a vehicle at 64,999 km must land on the
    65,000-km package and be DUE."""
    vt, mt, pkgs, v = milestone_profile
    v.current_odometer = 64999
    from app.extensions import db as _db
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(v)
    assert rec["recommended_package"].cumulative_km == 65000
    assert rec["due_odometer"] == 65000
    assert rec["status"] == "DUE"
    # Must NOT be the last package in the list.
    assert rec["recommended_package"] is not pkgs[-1]


def test_milestone_exactly_at_milestone_is_due_not_overdue(
        db, milestone_profile):
    vt, mt, pkgs, v = milestone_profile
    v.current_odometer = 65000
    from app.extensions import db as _db
    _db.session.commit()
    rec = PMPackageRecommendationService().recommend(v)
    assert rec["recommended_package"].cumulative_km == 65000
    assert rec["status"] == "DUE"


def test_milestone_just_past_advances_to_next_package(db, milestone_profile):
    vt, mt, pkgs, v = milestone_profile
    v.current_odometer = 65001
    from app.extensions import db as _db
    _db.session.commit()
    rec = PMPackageRecommendationService().recommend(v)
    assert rec["recommended_package"].cumulative_km == 70000
    assert rec["status"] == "UPCOMING"


def test_milestone_new_vehicle_gets_first_service(db, milestone_profile):
    vt, mt, pkgs, v = milestone_profile
    v.current_odometer = 100
    from app.extensions import db as _db
    _db.session.commit()
    rec = PMPackageRecommendationService().recommend(v)
    assert rec["recommended_package"].cumulative_km == 1000


def test_milestone_beyond_last_package_flags_fleet_manager_decision(
        db, milestone_profile):
    """Past the last defined milestone (70,000 km here): return the last
    package as a default and flag beyond_defined_cycle so the fleet
    manager decides which package to apply."""
    vt, mt, pkgs, v = milestone_profile
    v.current_odometer = 999999
    from app.extensions import db as _db
    _db.session.commit()
    rec = PMPackageRecommendationService().recommend(v)
    assert rec["beyond_defined_cycle"] is True
    assert rec["recommended_package"] is pkgs[-1]
    assert "fleet manager" in rec["reason"].lower()


def test_profile_ordering_sorts_null_sequence_last_without_nulls_last_sql(db):
    """Regression for a real production MySQL crash (1064 syntax error):
    the profile-package ordering must NOT emit the `NULLS LAST` SQL
    keyword, which MySQL rejects (only PostgreSQL/Oracle/SQLite accept
    it) -- so it must sort in Python, not via SQL ORDER BY. A package
    with a NULL sequence_position (which really occurs in imported data)
    must sort LAST, and the whole thing must not raise on any dialect."""
    from app.modules.maintenance_config.service import PMScheduleService
    mt = MaintenanceTypeService().create(code="PMS-NULLSEQ", name="PMS",
                                         category="PREVENTIVE")
    p_seq2 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        profile_code="NULLSEQ-PROF", sequence_position=2)
    p_seq1 = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
        profile_code="NULLSEQ-PROF", sequence_position=1)
    p_null = PMScheduleService().create(
        maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
        profile_code="NULLSEQ-PROF", sequence_position=None)

    ordered = PMPackageRecommendationService()._profile_packages(p_seq2)
    positions = [p.sequence_position for p in ordered]
    assert positions == [1, 2, None]  # NULL sorts last, no crash
