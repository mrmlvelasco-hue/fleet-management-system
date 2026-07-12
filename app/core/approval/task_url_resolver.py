"""Maps an ApprovalTask's reference_table to that module's own detail-page
URL, so the "For My Action" widget can link straight to the real
transaction (the existing Approve/Reject/Return buttons there are already
permission- and eligibility-gated — no new "unified view" needed for this
widget to be useful)."""
from flask import url_for

_ROUTE_MAP = {
    "trip_tickets": ("transactions.tripticket_detail", "tid"),
    "authority_to_drives": ("transactions.atd_detail", "aid"),
    "vehicle_movements": ("transactions.vehiclemovement_detail", "mid"),
    "maintenance_orders": ("transactions.maintenanceorder_detail", "oid"),
    "tire_transactions": ("transactions.tiretxn_detail", "tid"),
    "battery_transactions": ("transactions.batterytxn_detail", "bid"),
    "purchase_requests": ("transactions.purchaserequest_detail", "pid"),
    "vehicle_registrations": ("transactions.vehicleregistration_detail", "rid"),
}


def resolve_task_url(task) -> str | None:
    """Returns the detail-page URL for this task's underlying document,
    or None if the reference_table isn't recognized (future modules simply
    won't be clickable until added here — everything else about the task
    inbox works automatically without this map)."""
    entry = _ROUTE_MAP.get(task.reference_table)
    if entry is None:
        return None
    endpoint, param_name = entry
    return url_for(endpoint, **{param_name: task.reference_id})
