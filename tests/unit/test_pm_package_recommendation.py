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
    assert rec["due_odometer"] == 1900  # 900 baseline + 1000 interval
    assert rec["status"] in ("UPCOMING", "DUE")
    assert rec["due_by"] == "BOTH"


def test_high_odometer_no_history_does_not_dump_backlog(db, vehicle, profile):
    """The explicit requirement: a 60,000km vehicle with no logged PM
    history must be measured forward (next package only), NOT flagged
    with every interval from 1,000km up as overdue."""
    vehicle.current_odometer = 60000
    from app.extensions import db as _db
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    # Next due is measured FORWARD from 60,000, not a backlog.
    assert rec["due_odometer"] == 61000  # 60000 + 1000 (first package)
    assert rec["status"] == "UPCOMING"
    assert "61,000" in rec["reason"]


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

    vehicle.current_odometer = 5900
    _db.session.commit()

    rec = PMPackageRecommendationService().recommend(vehicle)
    assert rec["recommended_package"].sequence_position == 2
    assert rec["due_odometer"] == 6000  # 1000 last service + 5000 interval


def test_km_overdue_flagged(db, vehicle, profile):
    vt, mt, packages = profile
    svc = MaintenanceOrderService()
    mo = svc.create(vehicle_id=vehicle.id, maintenance_type_id=mt.id,
                    pm_schedule_id=packages[0].id,
                    scheduled_date=date.today(), user=None)
    mo.status = "COMPLETED"
    mo.completed_date = date.today()
    mo.odometer_at_service = 1000
    from app.extensions import db as _db
    _db.session.commit()

    vehicle.current_odometer = 6100  # past the 6000 due
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

    vehicle.current_odometer = 3000  # well under the 6000 KM due
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
