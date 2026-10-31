"""Vehicle assignment writers.

The ATD is a temporary driving permit and never changes a vehicle's
standing assignee. Formal vehicle assignment/reassignment remains the
responsibility of the completed Maintenance Order memo workflow, which
calls assign_driver_to_vehicle() directly.
"""
from app.extensions import db


def assign_driver_to_vehicle(vehicle_id: int, driver_id: int,
                             source: str = "MANUAL",
                             source_table: str = None,
                             source_id: int = None) -> None:
    """Shared update used by both trigger points below. Silently no-ops
    if either id is missing/invalid rather than raising -- a failed
    lookup here should never block an ATD approval or MO completion from
    going through; the assignment update is a side effect, not the main
    transaction.

    Now delegates to VehicleAssignmentService, so the handover is
    RECORDED rather than merely applied: the previous assignment is
    closed with an end date instead of being overwritten out of
    existence.

    `source`/`source_table`/`source_id` are optional so existing callers
    that pass only the two ids keep working unchanged. They are worth
    passing: both callers already know which document caused the change
    and used to discard it, and that is exactly what makes the history
    answer "why did this vehicle change hands on the 14th" rather than
    only "who holds it now".
    """
    if not vehicle_id or not driver_id:
        return
    from app.modules.master_data.vehicle.models import Vehicle
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return
    from app.modules.master_data.vehicle.assignment_service import (
        VehicleAssignmentService)
    VehicleAssignmentService().assign(
        vehicle_id, driver_id, source=source,
        source_table=source_table, source_id=source_id)


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
    # ATD grants temporary driving authority; it never changes the
    # vehicle's standing driver assignment.
    return


_HOOKS_REGISTERED = False


def register_vehicle_assignment_hooks() -> None:
    """Retain the legacy event subscription as a no-op for compatibility.

    ATD approval grants temporary authority; it is not a vehicle handover.
    """
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    from app.core.approval.engine import _subscribers
    _subscribers.append(_on_approval_event)
    _HOOKS_REGISTERED = True
