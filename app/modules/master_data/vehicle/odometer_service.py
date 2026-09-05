"""Recording odometer readings.

The single writer, for the same reason VehicleAssignmentService is: the
log and Vehicle.current_odometer must not be able to disagree, and the
only way to guarantee that is for one function to write both.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.odometer_models import VehicleOdometerLog


class OdometerError(Exception):
    """A message an administrator or driver can act on."""


class OdometerService:

    def record(self, vehicle_id, reading, *, source="WEB", user=None,
               source_table=None, source_id=None, remarks=None,
               allow_rollback=False):
        """Record a reading and move the vehicle's odometer.

        Both, in one transaction. A log written separately from the
        column is a log that eventually disagrees with it.

        Readings only go up unless `allow_rollback` -- a correction of a
        mis-keyed value is legitimate, but it should be a deliberate act
        rather than something a fat-fingered daily entry does silently,
        because current_odometer drives PM due status.
        """
        vehicle = db.session.get(Vehicle, vehicle_id)
        if vehicle is None:
            raise OdometerError("That vehicle does not exist.")
        try:
            reading = int(reading)
        except (TypeError, ValueError):
            raise OdometerError("Odometer must be a whole number.")
        if reading < 0:
            raise OdometerError("Odometer cannot be negative.")

        previous = vehicle.current_odometer
        if (previous is not None and reading < previous
                and not allow_rollback):
            raise OdometerError(
                f"Reading {reading:,} is lower than the last recorded "
                f"{previous:,}.")

        entry = VehicleOdometerLog(
            vehicle_id=vehicle_id, reading=reading, previous_reading=previous,
            source=source, source_table=source_table, source_id=source_id,
            remarks=remarks,
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
            recorded_by=getattr(user, "id", None))
        db.session.add(entry)
        vehicle.current_odometer = reading
        db.session.commit()
        return entry

    def history(self, vehicle_id=None, source=None, date_from=None,
                date_to=None, page=1, per_page=25):
        """Readings, newest first."""
        from sqlalchemy.orm import joinedload

        q = VehicleOdometerLog.query.options(
            joinedload(VehicleOdometerLog.vehicle),
            joinedload(VehicleOdometerLog.user),
        )
        if vehicle_id:
            q = q.filter_by(vehicle_id=vehicle_id)
        if source:
            q = q.filter_by(source=source)
        if date_from:
            q = q.filter(VehicleOdometerLog.recorded_at >= date_from)
        if date_to:
            q = q.filter(VehicleOdometerLog.recorded_at <= date_to)
        q = q.order_by(VehicleOdometerLog.recorded_at.desc(),
                       VehicleOdometerLog.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * per_page).limit(per_page).all()
        return rows, total
