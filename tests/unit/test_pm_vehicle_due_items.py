"""Every maintenance schedule that applies to ONE vehicle, by name.

Reported 2026-10-19 (Michael, mobile): "for the coming up for due the PMS
we not indicate what kind of PMS is this ... is this aircon servicing or
preventive maintenance or else."

get_due_status(vehicle) answers for the FIRST applicable maintenance type
only, so the phone could neither name the PMS nor show a second one (an
Aircon Servicing schedule was invisible whenever a Preventive
Maintenance schedule also applied). vehicle_due_items() evaluates every
maintenance type independently -- the same rule get_all_due_vehicles()
already follows for the dashboard -- and names each one.
"""
from datetime import date, timedelta

import pytest

from app.core.maintenance.due_calculation_service import (
    PMDueCalculationService)
from app.extensions import db
from app.modules.maintenance_config.service import PMScheduleService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.models import MaintenanceOrder


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VDI", name="Due Items")
    vt = VehicleTypeService().create(code="LV-VDI", name="Light",
                                     category="LIGHT")
    pms = MaintenanceTypeService().create(
        code="PMS-VDI", name="Preventive Maintenance Service",
        category="PREVENTIVE")
    aircon = MaintenanceTypeService().create(
        code="AC-VDI", name="Aircon Servicing", category="PREVENTIVE")
    PMScheduleService().create(vehicle_type_id=vt.id,
                               maintenance_type_id=pms.id,
                               trigger_mode="KM", interval_km=5000)
    PMScheduleService().create(vehicle_type_id=vt.id,
                               maintenance_type_id=aircon.id,
                               trigger_mode="KM", interval_km=20000)
    v = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2021,
        branch_id=branch.id, conduction_number="VDI-1",
        current_odometer=4800)
    db.session.commit()
    return dict(v=v, pms=pms, aircon=aircon, vt=vt)


def _by_name(items):
    return {i["maintenance_type"]: i for i in items}


def test_every_applicable_type_is_listed_by_name(db, env):
    items = _by_name(PMDueCalculationService().vehicle_due_items(env["v"]))
    assert set(items) == {"Preventive Maintenance Service",
                          "Aircon Servicing"}
    assert items["Preventive Maintenance Service"]["status"] == "DUE_SOON"
    assert items["Preventive Maintenance Service"]["next_due_km"] == 5000
    assert items["Aircon Servicing"]["status"] == "GOOD"
    assert items["Aircon Servicing"]["next_due_km"] == 20000


def test_most_urgent_first(db, env):
    env["v"].current_odometer = 20500  # both overdue; PMS by more
    db.session.commit()
    items = PMDueCalculationService().vehicle_due_items(env["v"])
    assert [i["status"] for i in items] == ["OVERDUE", "OVERDUE"]
    assert items[0]["maintenance_type"] == "Preventive Maintenance Service"

    env["v"].current_odometer = 1000  # both good; PMS sooner
    db.session.commit()
    items = PMDueCalculationService().vehicle_due_items(env["v"])
    assert items[0]["maintenance_type"] == "Preventive Maintenance Service"

    # PMS just done at 19,000 (next 24,000); aircon due at 20,000 is now
    # the one coming up -- it must lead, whatever the names sort to.
    db.session.add(MaintenanceOrder(
        document_number="MO-VDI-PMS", vehicle_id=env["v"].id,
        maintenance_type_id=env["pms"].id, order_category="MAINTENANCE",
        status="COMPLETED", scheduled_date=date.today(),
        completed_date=date.today(), odometer_at_service=19000))
    env["v"].current_odometer = 19900
    db.session.commit()
    items = PMDueCalculationService().vehicle_due_items(env["v"])
    assert [(i["maintenance_type"], i["status"]) for i in items] == [
        ("Aircon Servicing", "DUE_SOON"),
        ("Preventive Maintenance Service", "GOOD")]


def test_a_directly_assigned_schedule_does_not_hide_other_types(db, env):
    """The single-vehicle get_due_status() stops at the vehicle's own
    pm_schedule_id; that is exactly how an aircon schedule went missing."""
    from app.modules.maintenance_config.models import PMSchedule
    own = PMSchedule.query.filter_by(
        maintenance_type_id=env["pms"].id).first()
    env["v"].pm_schedule_id = own.id
    db.session.commit()
    names = [i["maintenance_type"]
             for i in PMDueCalculationService().vehicle_due_items(env["v"])]
    assert sorted(names) == ["Aircon Servicing",
                             "Preventive Maintenance Service"]


def test_carries_interval_and_last_service(db, env):
    db.session.add(MaintenanceOrder(
        document_number="MO-VDI-1", vehicle_id=env["v"].id,
        maintenance_type_id=env["aircon"].id, order_category="MAINTENANCE",
        status="COMPLETED", scheduled_date=date.today() - timedelta(days=9),
        completed_date=date.today() - timedelta(days=9),
        odometer_at_service=3000))
    db.session.commit()
    item = _by_name(PMDueCalculationService().vehicle_due_items(
        env["v"]))["Aircon Servicing"]
    assert item["interval_km"] == 20000
    assert item["last_km"] == 3000
    assert item["next_due_km"] == 23000


def test_an_open_order_for_that_type_is_attached(db, env):
    db.session.add(MaintenanceOrder(
        document_number="MO-VDI-OPEN", vehicle_id=env["v"].id,
        maintenance_type_id=env["pms"].id, order_category="MAINTENANCE",
        status="DRAFT", scheduled_date=date.today()))
    db.session.commit()
    items = _by_name(PMDueCalculationService().vehicle_due_items(env["v"]))
    assert (items["Preventive Maintenance Service"]["open_order"]
            ["document_number"]) == "MO-VDI-OPEN"
    assert items["Aircon Servicing"]["open_order"] is None


def test_no_schedules_is_an_empty_list(db, env):
    other = VehicleTypeService().create(code="HV-VDI", name="Heavy",
                                        category="HEAVY")
    branch = BranchService().list()[0]
    v = VehicleService().create(
        vehicle_type_id=other.id, brand="Isuzu", model="Elf", year=2020,
        branch_id=branch.id, conduction_number="VDI-2")
    db.session.commit()
    assert PMDueCalculationService().vehicle_due_items(v) == []
