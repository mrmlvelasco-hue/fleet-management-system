"""PM Parameter Mapping token resolver.

Checklist activity descriptions, work descriptions, and remarks
(especially from imported legacy data) can contain placeholder tokens
like "pm8" that are meant to be substituted with real, live data at
print time — this is the same "PM Parameter Mapping" legend from the
Dynamic PM Work Order Report spec (pm2=Vehicle Make ... pm8=Last Work
Order), just applied as genuine text substitution rather than only
showing these values in separate table rows.

Reusable across every print report — every transaction module has a
`.vehicle` relationship, so the same resolver works for Maintenance
Orders, ATDs, Trip Tickets, Vehicle Registrations, etc. without each
module needing its own version.
"""
import re

PM_TOKEN_LABELS = {
    "pm2": "Vehicle Make",
    "pm3": "Vehicle Model",
    "pm4": "Vehicle Plate Number",
    "pm5": "Assignee",
    "pm6": "Sales Office / Branch",
    "pm7": "Assignee Position",
    "pm8": "Last Work Order",
    "pm9": "Last Work Order Completed Date",
}

_TOKEN_PATTERN = re.compile(r"\bpm\d+\b", re.IGNORECASE)


def _last_completed_maintenance_order(vehicle):
    if vehicle is None:
        return None
    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    return (MaintenanceOrder.query
           .filter_by(vehicle_id=vehicle.id, status="COMPLETED")
           .filter(MaintenanceOrder.completed_date.isnot(None))
           .order_by(MaintenanceOrder.completed_date.desc())
           .first())


def resolve_pm_tokens(text, vehicle, last_work_order=None) -> str:
    """Replaces every recognized pmN token in `text` with live data about
    `vehicle`. Unrecognized tokens (e.g. "pm99") are left untouched
    rather than blanked out, since that's more likely a typo worth
    noticing than something to silently hide. Missing/unavailable data
    (no assigned driver, no prior work order, no vehicle at all) resolves
    to an empty string rather than raising — a print report should never
    crash because one optional field wasn't available.

    `last_work_order` can be passed explicitly (e.g. a route that already
    computed it) to avoid a redundant query; otherwise it's looked up
    automatically from the vehicle's Maintenance Order history."""
    if not text:
        return text

    driver = getattr(vehicle, "assigned_driver", None) if vehicle else None
    branch = getattr(vehicle, "branch", None) if vehicle else None
    if last_work_order is None:
        last_work_order = _last_completed_maintenance_order(vehicle)

    values = {
        "pm2": getattr(vehicle, "brand", None) or "",
        "pm3": getattr(vehicle, "model", None) or "",
        "pm4": (getattr(vehicle, "plate_number", None)
               or getattr(vehicle, "conduction_number", None) or "") if vehicle else "",
        "pm5": driver.full_name if driver else "",
        "pm6": branch.name if branch else "",
        "pm7": (driver.job_title or "") if driver else "",
        "pm8": last_work_order.document_number if last_work_order and last_work_order.document_number else "",
        "pm9": (last_work_order.completed_date.strftime("%Y-%m-%d")
               if last_work_order and last_work_order.completed_date else ""),
    }

    def _substitute(match):
        token = match.group(0).lower()
        return values.get(token, match.group(0))

    return _TOKEN_PATTERN.sub(_substitute, text)
