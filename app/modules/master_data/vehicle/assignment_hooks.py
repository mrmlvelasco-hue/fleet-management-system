"""Vehicle Assignment Hooks — subscribes to ApprovalEngine events so the
Vehicle master's "Assigned Driver" field (and therefore the Assignee
column in Vehicle Register Details / the dashboard Vehicle List) reflects
reality without a separate manual step.

Why this exists: there are two things in the app that look like they
should "assign a driver to a vehicle" but neither one, on its own,
previously updated Vehicle.assigned_driver_id:

  1. Authority To Drive (ATD) — explicitly authorizes a specific driver
     for a specific vehicle over a date range. This is the natural
     source of truth for "who is currently assigned/authorized to drive
     this vehicle."
  2. A Maintenance Order with Category=Operational and Transaction
     Type=Assignment — this is a work-order record of an assignment
     *activity* (e.g. paperwork, handover checklist) but carries no
     automation of its own; it's descriptive metadata, not an
     assignment mechanism.

Only Vehicle Master's own "Assigned Driver" field ever actually updated
Vehicle.assigned_driver_id before this hook existed — via a direct edit,
with no connection to ATD or MO at all. That's why the Vehicle Register
Details report kept showing "—" for vehicles that clearly had an
approved ATD assigning a driver.

This hook makes ATD final approval the trigger: when an ATD's approval
reaches approved_final, the vehicle's assigned_driver_id is set to the
ATD's driver_id. Operational/Assignment MOs remain administrative
records only and do not (yet) drive this automatically — see the note
in _seed... on how to extend this if that's also wanted.
"""
from app.extensions import db


def _on_approval_event(event_name: str, instance) -> None:
    if event_name != "approved_final":
        return
    if instance.reference_table != "authority_to_drives":
        return

    from app.modules.transactions.atd.models import AuthorityToDrive
    from app.modules.master_data.vehicle.models import Vehicle

    atd = db.session.get(AuthorityToDrive, instance.reference_id)
    if atd is None or not atd.vehicle_id or not atd.driver_id:
        return

    vehicle = db.session.get(Vehicle, atd.vehicle_id)
    if vehicle is None:
        return

    vehicle.assigned_driver_id = atd.driver_id
    db.session.commit()


_HOOKS_REGISTERED = False


def register_vehicle_assignment_hooks() -> None:
    """Subscribe to ApprovalEngine events. Called once in the app
    factory, alongside register_notification_hooks(). Guarded against
    double-registration the same way — see the matching guard in
    notification_engine.register_notification_hooks() for why."""
    global _HOOKS_REGISTERED
    if _HOOKS_REGISTERED:
        return
    from app.core.approval.engine import _subscribers
    _subscribers.append(_on_approval_event)
    _HOOKS_REGISTERED = True
