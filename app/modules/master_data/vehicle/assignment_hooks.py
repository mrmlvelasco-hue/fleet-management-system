"""Vehicle Assignment Hooks — updates Vehicle.assigned_driver_id from two
independent sources, so the Assignee column in Vehicle Register Details /
the dashboard Vehicle List reflects reality without a separate manual
step, no matter which document your process uses to record the change:

  1. Authority To Drive (ATD) final approval — an ATD explicitly
     authorizes a specific driver for a specific vehicle. Subscribes to
     the ApprovalEngine event bus (same mechanism as the notification
     hooks): when an ATD's approval reaches approved_final, the vehicle's
     assigned_driver_id is set from the ATD's driver_id.

  2. A Maintenance Order (Category=Operational, Transaction
     Type=Assignment/Reassignment/Relocation/Transfer) reaching
     COMPLETED, with its Driver / Assignee field set — this is the
     "Vehicle Assignment Memo" workflow: a formal paper-trail document
     for the handover, called directly from
     MaintenanceOrderService.complete() rather than the approval-event
     bus, since MO completion is a separate physical-lifecycle action
     from approval (see the MO model's own status field).

Both mechanisms call the same assign_driver_to_vehicle() below, so
either one can be "the" operative record for a given handover -- a quick
ATD-only reassignment, or a formal MO memo, whichever fits the situation
(e.g. a temporary CASA/service-center custody during PMS, followed by a
second ATD or MO once the vehicle returns to its regular driver).

Before this existed, NEITHER of the above updated Vehicle.assigned_driver_id
at all -- only a direct manual edit on the Vehicle Master form did, which
is why the Vehicle Register Details report kept showing "—" even with an
approved ATD or a completed Assignment MO on file.
"""
from app.extensions import db


def assign_driver_to_vehicle(vehicle_id: int, driver_id: int) -> None:
    """Shared update used by both trigger points below. Silently no-ops
    if either id is missing/invalid rather than raising -- a failed
    lookup here should never block an ATD approval or MO completion from
    going through; the assignment update is a side effect, not the main
    transaction."""
    if not vehicle_id or not driver_id:
        return
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return
    vehicle.assigned_driver_id = driver_id
    db.session.commit()


def transfer_vehicle_branch(vehicle_id: int, destination_branch_id: int) -> None:
    """Moves a vehicle to a new branch -- called from
    MaintenanceOrderService.complete() when an Operational order
    (Transaction Type Relocation/Transfer) with a destination branch set
    is completed. Same no-op-on-missing-data safety as
    assign_driver_to_vehicle above."""
    if not vehicle_id or not destination_branch_id:
        return
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return
    vehicle.branch_id = destination_branch_id
    db.session.commit()


def _on_approval_event(event_name: str, instance) -> None:
    if event_name != "approved_final":
        return
    if instance.reference_table != "authority_to_drives":
        return

    from app.modules.transactions.atd.models import AuthorityToDrive

    atd = db.session.get(AuthorityToDrive, instance.reference_id)
    if atd is None:
        return
    assign_driver_to_vehicle(atd.vehicle_id, atd.driver_id)


_HOOKS_REGISTERED = False


def register_vehicle_assignment_hooks() -> None:
    """Subscribe the ATD trigger to ApprovalEngine events. Called once in
    the app factory, alongside register_notification_hooks(). Guarded
    against double-registration the same way — see the matching guard in
    notification_engine.register_notification_hooks() for why."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    from app.core.approval.engine import _subscribers
    _subscribers.append(_on_approval_event)
    _HOOKS_REGISTERED = True
