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
                date_to=None, plate=None, page=1, per_page=25, user=None):
        """Readings, newest first.

        `user`, when supplied, applies the same two-layer visibility rule
        as everywhere else a vehicle list is scoped:

          Vehicle Assignee -> ONLY vehicles currently assigned to them
                              (AssigneeScopeService), never falling back
                              to org scope even if it would be wider.
          Everyone else    -> org scope (UserOrgScopeService): their own
                              branch(es), or every branch if they hold no
                              scope rows or hold GLOBAL/COMPANY.

        This mirrors VehicleService.list_page()'s filter exactly, which
        is deliberate -- the odometer list and the vehicle list must
        describe the same fleet for the same person, or a user could see
        a vehicle in one screen and not the other with no way to tell
        why.

        Found unscoped entirely: the endpoint carried only
        vehicle.view, so anyone holding that one permission saw every
        vehicle's odometer history company-wide.
        """
        from sqlalchemy import or_
        from sqlalchemy.orm import joinedload
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.user_management.assignee_scope_service import (
            AssigneeScopeService)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

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

        # Resolved BEFORE any join: which branch ids (if any) restrict
        # this user, decided once, so the join to `vehicles` below is
        # added at most once regardless of how many filters need it.
        # Joining twice on the same query -- once for plate search, once
        # for branch scope -- previously produced "ambiguous column
        # name" the moment BOTH applied at once, which a test exercising
        # only one filter at a time would never have shown.
        assignee_vehicle_ids = None   # None = not an assignee
        branch_ids = None             # None = unrestricted / not applicable

        if user is not None:
            assignee = AssigneeScopeService().assignee_for(user)
            if assignee is not None:
                # An assignee's world is their own vehicles ONLY. This
                # must not fall back to org scope for a driver who holds
                # none right now -- that would silently widen to the
                # whole branch, which is the exact leak closed elsewhere
                # for checklists and vehicle creation.
                assignee_vehicle_ids = (
                    AssigneeScopeService().assigned_vehicle_ids(user))
            else:
                scopes = UserOrgScopeService().list_for_user(user.id)
                # No scope rows at all, or an explicit GLOBAL/COMPANY
                # grant, both mean unrestricted -- the same
                # backward-compatibility rule covers() and
                # list_page() apply.
                if scopes and not any(
                        s.scope_type in ("GLOBAL", "COMPANY")
                        for s in scopes):
                    branch_ids = [s.branch_id for s in scopes
                                 if s.scope_type == "BRANCH"
                                 and s.branch_id]

        if assignee_vehicle_ids is not None:
            q = q.filter(VehicleOdometerLog.vehicle_id.in_(
                assignee_vehicle_ids) if assignee_vehicle_ids else False)

        if plate or branch_ids is not None:
            q = q.join(Vehicle,
                      VehicleOdometerLog.vehicle_id == Vehicle.id)
        if plate:
            like = f"%{plate.lower()}%"
            q = q.filter(or_(Vehicle.plate_number.ilike(like),
                             Vehicle.conduction_number.ilike(like)))
        if branch_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(branch_ids)
                        if branch_ids else False)

        q = q.order_by(VehicleOdometerLog.recorded_at.desc(),
                       VehicleOdometerLog.id.desc())
        total = q.count()
        rows = q.offset((page - 1) * per_page).limit(per_page).all()
        return rows, total
