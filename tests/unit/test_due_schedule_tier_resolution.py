"""Schedule matching resolves per MAINTENANCE TYPE, not once per vehicle.

Michael: a Tire Replacement PM schedule was configured and KM-driven,
the vehicles were well past the interval, and no tire row ever appeared
on "Vehicles Due for Maintenance" -- while Battery Replacement and
Aircon Servicing on the SAME vehicles appeared fine.

_resolve_schedules() walks tiers most-specific-first and returns the
first non-empty one:

    FK (brand_id+model_id) -> make/model text -> vehicle type -> global

That is right when resolving ONE maintenance type. But
get_all_due_vehicles() calls it with maintenance_type_id=None to
resolve ALL types at once, and then the first non-empty tier wins for
every type simultaneously. Any type whose schedule lives at a LOWER
tier than the vehicle's best-matching tier becomes invisible.

Confirmed against the live data: all 122 active tire schedules carry
vehicle_brand_id + vehicle_model_id and no make/model text, so they sit
in the FK and global tiers. The PM/Battery/Aircon schedules use the
legacy make/model text fields. A vehicle without brand/model FKs skips
the FK tier, matches make/model text, returns those three, and stops --
the tire schedule below is never reached.

Nothing tire-specific about it: tires were simply the type configured
at a different tier from the rest.
"""
import pytest

from app.core.maintenance.due_calculation_service import PMDueCalculationService
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import MaintenanceType, VehicleType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.maintenance_config.models import PMSchedule


@pytest.fixture()
def rig(db):
    branch = Branch(name="HQ", code="HQ")
    db.session.add(branch)
    vtype = VehicleType(code="CARL", name="Car Light", category="CAR")
    db.session.add(vtype)
    db.session.flush()

    prev = MaintenanceType(code="PMS-PREV", name="Preventive Maintenance Service",
                          category="PM")
    tire = MaintenanceType(code="PMS-TIRE", name="Tire Replacement", category="PM")
    batt = MaintenanceType(code="PMS-BATT", name="Battery Replacement",
                          category="PM")
    db.session.add_all([prev, tire, batt])
    db.session.flush()

    # The reported shape: a vehicle identified by make/model TEXT, with
    # no brand/model FKs -- so the FK tier cannot match it.
    vehicle = Vehicle(plate_number="NZO-619", brand="Toyota", model="Vios",
                     year=2009, vehicle_type_id=vtype.id, branch_id=branch.id,
                     is_active=True, status="ACTIVE", current_odometer=45100)
    db.session.add(vehicle)
    db.session.flush()

    # PM + Battery at the make/model TEXT tier.
    for mt in (prev, batt):
        db.session.add(PMSchedule(
            maintenance_type_id=mt.id, vehicle_make="Toyota",
            vehicle_model="Vios", trigger_mode="KM", interval_km=5000,
            is_active=True))
    # Tire at the GLOBAL tier -- no make/model text, no vehicle type.
    db.session.add(PMSchedule(
        maintenance_type_id=tire.id, trigger_mode="KM", interval_km=40000,
        is_active=True))
    db.session.commit()
    return {"vehicle": vehicle, "prev": prev, "tire": tire, "batt": batt}


def _type_ids(schedules):
    return {s.maintenance_type_id for s in schedules}


class TestAllTypesResolve:
    def test_a_lower_tier_type_is_not_suppressed_by_a_higher_tier_one(
            self, db, rig):
        """The reported bug, at its source.

        Resolving ALL types must not let the make/model tier hide a
        type that only exists globally.
        """
        from app.core.maintenance.due_calculation_service import _Prefetch
        schedules = _Prefetch().applicable_schedules(rig["vehicle"])
        assert rig["tire"].id in _type_ids(schedules), (
            "the tire schedule lives at the global tier and was dropped "
            "because the make/model tier matched first")

    def test_the_higher_tier_types_are_still_returned(self, db, rig):
        """The fix must ADD the missing type, not swap which ones are
        lost -- returning tire while dropping PM and battery would be
        the same bug pointing the other way."""
        from app.core.maintenance.due_calculation_service import _Prefetch
        ids = _type_ids(_Prefetch().applicable_schedules(rig["vehicle"]))
        assert rig["prev"].id in ids
        assert rig["batt"].id in ids

    def test_most_specific_still_wins_WITHIN_a_type(self, db, rig):
        """Specificity is preserved where it actually means something.

        Two schedules for the SAME type at different tiers: the more
        specific one must win. That is the documented intent, and this
        fix must not flatten it into "return everything".
        """
        from app.core.maintenance.due_calculation_service import _Prefetch
        specific = PMSchedule(
            maintenance_type_id=rig["tire"].id, vehicle_make="Toyota",
            vehicle_model="Vios", trigger_mode="KM", interval_km=30000,
            is_active=True)
        db.session.add(specific)
        db.session.commit()

        schedules = _Prefetch().applicable_schedules(rig["vehicle"])
        tire_scheds = [s for s in schedules
                      if s.maintenance_type_id == rig["tire"].id]
        assert len(tire_scheds) == 1, "one schedule per type, most specific"
        assert tire_scheds[0].id == specific.id
        assert tire_scheds[0].interval_km == 30000

    def test_filtering_by_one_type_is_unchanged(self, db, rig):
        """Single-type resolution was always correct and must stay so."""
        from app.core.maintenance.due_calculation_service import _Prefetch
        schedules = _Prefetch().applicable_schedules(
            rig["vehicle"], maintenance_type_id=rig["tire"].id)
        assert _type_ids(schedules) == {rig["tire"].id}


class TestDueListIncludesTires:
    def test_the_overdue_tire_appears_in_the_due_list(self, db, rig):
        """End to end, the way Michael sees it.

        45,100 km against a 40,000 km tire interval with no prior
        service is overdue, so the row must be there.
        """
        due = PMDueCalculationService().get_all_due_vehicles()
        types = {d["schedule"].maintenance_type_id for d in due
                if d["vehicle"].id == rig["vehicle"].id}
        assert rig["tire"].id in types

    def test_an_open_order_still_suppresses_its_own_type_only(self, db, rig):
        """The exclusion that explained NFO-899 is correct and stays.

        An open tire order hides the TIRE row -- the work is raised --
        without touching the others.
        """
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        from datetime import date
        db.session.add(MaintenanceOrder(
            vehicle_id=rig["vehicle"].id,
            maintenance_type_id=rig["tire"].id,
            document_number="MO-TEST-1", status="DRAFT",
            scheduled_date=date.today()))
        db.session.commit()

        due = PMDueCalculationService().get_all_due_vehicles()
        types = {d["schedule"].maintenance_type_id for d in due
                if d["vehicle"].id == rig["vehicle"].id}
        assert rig["tire"].id not in types
        assert rig["prev"].id in types
