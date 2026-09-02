"""The single writer for vehicle assignment.

Three workflows change who holds a vehicle -- a manual edit on the
Vehicle form, ATD final approval, and completion of an Operational
Maintenance Order (the Vehicle Assignment Memo). Before this, each one
set `Vehicle.assigned_driver_id` directly and the previous assignment
simply vanished.

All three now funnel through assign()/release() here. That funnelling is
what keeps the history and the column from disagreeing: they are written
in the same transaction, by the same method, or not at all.

The one-open-row-per-vehicle invariant is enforced HERE rather than by a
database constraint. The natural expression is a partial unique index
(UNIQUE ... WHERE assigned_to IS NULL); MySQL has no filtered indexes,
and the schema must stay migratable to SQL Server per the master prompt.
A constraint that only existed on one engine would give false confidence
on the others, so the rule lives in the one method every writer calls.
"""
from datetime import date

from app.extensions import db
from app.modules.master_data.vehicle.assignment_models import VehicleAssignment
from app.modules.master_data.vehicle.models import Vehicle


class VehicleAssignmentService:

    def assign(self, vehicle_id, driver_id, *, source="MANUAL",
               source_table=None, source_id=None, assigned_from=None,
               remarks=None, user=None):
        """Hand a vehicle to an assignee.

        Closes whatever assignment was open, opens a new one, and
        updates the cached column -- one transaction, so the history and
        the column cannot end up disagreeing.

        Returns the open assignment row, or None if either id is
        unusable.
        """
        if not vehicle_id or not driver_id:
            return None
        vehicle = db.session.get(Vehicle, vehicle_id)
        if vehicle is None:
            return None

        existing = self.current_for_vehicle(vehicle_id)

        # Idempotent. Saving the Vehicle form twice without changing the
        # assignee must not close and reopen the assignment: that would
        # record a handover that never happened, and an audit table
        # documenting the act of pressing Save is worse than no audit
        # table at all.
        if existing is not None and existing.driver_id == driver_id:
            # The column is still reconciled, in case it drifted from
            # the history through some path that predates this service.
            if vehicle.assigned_driver_id != driver_id:
                vehicle.assigned_driver_id = driver_id
                db.session.commit()
            return existing

        today = date.today()
        if existing is not None:
            existing.assigned_to = today

        row = VehicleAssignment(
            vehicle_id=vehicle_id, driver_id=driver_id,
            assigned_from=assigned_from or today,
            assigned_to=None, source=source,
            source_table=source_table, source_id=source_id,
            remarks=remarks)
        db.session.add(row)

        # The cache the rest of the system reads. Three places filter it
        # in SQL and every vehicle template renders it; if it does not
        # move with the history, every one of those screens starts
        # lying.
        vehicle.assigned_driver_id = driver_id

        db.session.commit()
        return row

    def release(self, vehicle_id, *, source="MANUAL", user=None):
        """End the current assignment without starting another.

        Harmless on a vehicle that holds no open assignment -- releasing
        something already released is not an error, and raising here
        would mean every caller had to check first.
        """
        vehicle = db.session.get(Vehicle, vehicle_id)
        if vehicle is None:
            return None
        existing = self.current_for_vehicle(vehicle_id)
        if existing is not None:
            existing.assigned_to = date.today()
        vehicle.assigned_driver_id = None
        db.session.commit()
        return existing

    def current_for_vehicle(self, vehicle_id):
        """The open assignment, or None.

        `assigned_to IS NULL` is the definition of "currently held", and
        every scope decision in Phase 2 rests on this one predicate.
        """
        return (VehicleAssignment.query
                .filter_by(vehicle_id=vehicle_id, assigned_to=None)
                .order_by(VehicleAssignment.id.desc())
                .first())

    def current_for_driver(self, driver_id):
        """Open assignments held by one assignee.

        A LIST, not a single row: one person holding several vehicles is
        supported and already happens in practice, which is why Phase
        2's endpoint is /my/vehicles rather than /my/vehicle.
        """
        return (VehicleAssignment.query
                .filter_by(driver_id=driver_id, assigned_to=None)
                .order_by(VehicleAssignment.id).all())

    def history_for_vehicle(self, vehicle_id):
        """Every assignment this vehicle has had, newest first."""
        return (VehicleAssignment.query
                .filter_by(vehicle_id=vehicle_id)
                .order_by(VehicleAssignment.id.desc()).all())
