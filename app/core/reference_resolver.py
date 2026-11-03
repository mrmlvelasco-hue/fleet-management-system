"""Generic reference_table -> model/display resolver.

Notification emails only ever carry reference_table + reference_id (e.g.
"maintenance_orders", 11) since that's the generic key every module's
approval/comment/attachment rows already use. This module is the one
place that knows how to turn that pair into something a person actually
reads: a real document number (MO-2026-000011, not "maintenance_orders
#11"), a link back into the app, and any attached files.

Add a new transaction module here and its emails automatically get real
document numbers -- no per-module changes needed elsewhere.
"""

# reference_table -> (model import path, model class name, url endpoint)
# url endpoint takes the record's primary key as `oid`/`rid`/etc. per each
# blueprint's own convention, so it's stored as a callable below instead.
_REGISTRY = {
    "maintenance_orders": (
        "app.modules.transactions.maintenance_order.models",
        "MaintenanceOrder",
        lambda id_: ("transactions.maintenanceorder_detail", {"oid": id_})),
    "purchase_requests": (
        "app.modules.transactions.purchase_request.models",
        "PurchaseRequest",
        lambda id_: ("transactions.purchaserequest_detail", {"pid": id_})),
    "trip_tickets": (
        "app.modules.transactions.trip_ticket.models",
        "TripTicket",
        lambda id_: ("transactions.tripticket_detail", {"tid": id_})),
    "authority_to_drives": (
        "app.modules.transactions.atd.models",
        "AuthorityToDrive",
        lambda id_: ("transactions.atd_detail", {"aid": id_})),
    "vehicle_movements": (
        "app.modules.transactions.vehicle_movement.models",
        "VehicleMovement",
        lambda id_: ("transactions.vehiclemovement_detail", {"mid": id_})),
    "vehicle_registrations": (
        "app.modules.transactions.vehicle_registration.models",
        "VehicleRegistration",
        lambda id_: ("transactions.vehicleregistration_detail", {"rid": id_})),
    "tire_transactions": (
        "app.modules.transactions.tire_txn.models",
        "TireTransaction",
        lambda id_: ("transactions.tiretxn_detail", {"tid": id_})),
    "battery_transactions": (
        "app.modules.transactions.battery_txn.models",
        "BatteryTransaction",
        lambda id_: ("transactions.batterytxn_detail", {"bid": id_})),
    "maintenance_invoices": (
        "app.modules.transactions.maintenance_invoice.models",
        "MaintenanceInvoice",
        lambda id_: ("transactions.maintenanceinvoice_detail", {"iid": id_})),
}


def get_document_number(reference_table: str, reference_id: int) -> str:
    """Real document number (e.g. "MO-2026-000011") for display in
    notification emails and elsewhere, falling back to a readable
    "<table> #<id>" if the table isn't registered or the record has no
    number yet (e.g. still DRAFT)."""
    entry = _REGISTRY.get(reference_table)
    fallback = f"{reference_table} #{reference_id}"
    if entry is None:
        return fallback
    module_path, class_name, _ = entry
    try:
        import importlib
        module = importlib.import_module(module_path)
        model = getattr(module, class_name)
        from app.extensions import db
        record = db.session.get(model, reference_id)
        if record is None:
            return fallback
        return getattr(record, "document_number", None) or fallback
    except Exception:
        return fallback


def _load_record(reference_table: str, reference_id: int):
    """The registered model row for (reference_table, reference_id), or
    None when the table is unregistered, the row is missing, or the
    lookup fails for any reason -- notification rendering must never
    break because one optional detail could not be resolved."""
    entry = _REGISTRY.get(reference_table)
    if entry is None:
        return None
    module_path, class_name, _ = entry
    try:
        import importlib
        module = importlib.import_module(module_path)
        model = getattr(module, class_name)
        from app.extensions import db
        return db.session.get(model, reference_id)
    except Exception:
        return None


def get_description(reference_table: str, reference_id: int) -> str:
    """A one-line human summary of the document -- for notification
    messages and email subjects. Duck-typed: any registered model with
    a `notification_description` property gets its value; every other
    document returns "".

    Examples:
      maintenance_orders  -> "Corrective MO · Isuzu Elf NAO-907"
      trip_tickets        -> "Trip Ticket TT-2026-000012 · Juan Driver"
    """
    record = _load_record(reference_table, reference_id)
    if record is None:
        return ""
    try:
        val = getattr(record, "notification_description", None)
        return str(val).strip() if val else ""
    except Exception:
        return ""


def get_scope_items(reference_table: str, reference_id: int) -> list:
    """Scope of Work line items for a document, as
    [{"code": str, "description": str}, ...] -- for the
    `{{ scope_items }}` email context variable.

    Duck-typed: any registered model exposing a `scope_of_work_lines`
    property (today, MaintenanceOrder) gets its lines into emails with no
    change here. Every other document returns [] so templates can always
    write `{% if scope_items %}` safely.
    """
    record = _load_record(reference_table, reference_id)
    if record is None:
        return []
    try:
        lines = getattr(record, "scope_of_work_lines", None) or []
        return [{"code": str(line.get("code") or ""),
                 "description": str(line.get("description") or "")}
                for line in lines]
    except Exception:
        return []


# reference_table -> the React app's own route for that document
# (confirmed against fms_fleet_react's src/App.tsx route list). This is
# deliberately separate from _REGISTRY above: _REGISTRY's url_fn points
# at Flask's own legacy server-rendered pages, which is a different app
# on a different origin than the one people actually work in day to day
# (the React web app and its mobile build) -- the right page for an
# email link to open, and the only one Flask's url_for() can never
# resolve on its own (it has no idea the React app even exists).
_FRONTEND_PATHS = {
    "maintenance_orders": "/maintenance-orders/{id}",
    "purchase_requests": "/purchase-requests/{id}",
    "trip_tickets": "/trip-tickets/{id}",
    "authority_to_drives": "/atd/{id}",
    "vehicle_movements": "/vehicle-movements/{id}",
    "vehicle_registrations": "/vehicle-registrations/{id}",
    "tire_transactions": "/tire-transactions/{id}",
    "battery_transactions": "/battery-transactions/{id}",
}


def get_view_url(reference_table: str, reference_id: int) -> str | None:
    """Absolute URL into the React app for this document, for the "Open
    this document" link in notification emails.

    Built from the APP_PUBLIC_URL System Parameter (System
    Administration -> System Parameters, group NOTIFICATIONS) plus this
    document's own frontend route. Returns None -- never a broken link
    -- whenever that parameter isn't set, or the table has no known
    frontend page: an email with no link reads fine; one with a dead
    link does not.

    Previously built with Flask's own url_for(), with two bugs at once:
    no _external=True (so the href was a bare path, meaningless with no
    "current page" for an email to resolve it against), and pointing at
    the old server-rendered Jinja page rather than the React app people
    actually use (see _FRONTEND_PATHS above).

    maintenance_invoices has no page of its own -- it's a panel on its
    Maintenance Order's detail page -- so it resolves to its PARENT
    MO's URL instead.
    """
    try:
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        base = (SystemParameterService().get("APP_PUBLIC_URL", "") or "").strip()
    except Exception:
        return None
    if not base:
        return None
    base = base.rstrip("/")

    if reference_table == "maintenance_invoices":
        record = _load_record(reference_table, reference_id)
        mo_id = getattr(record, "maintenance_order_id", None)
        if mo_id is None:
            return None
        reference_table, reference_id = "maintenance_orders", mo_id

    path = _FRONTEND_PATHS.get(reference_table)
    if path is None:
        return None
    return base + path.format(id=reference_id)


def get_worklist_labels(reference_table: str, reference_id: int) -> dict:
    """Plate number + a human-readable type/purpose label for a document,
    for the "For My Action" dashboard worklist — so an approver can tell
    at a glance which vehicle and what kind of request each entry is
    without opening it first. Generic across every transaction module
    rather than special-cased per type, since ApprovalTask.reference_table
    can be any of them: looks for a `vehicle` relationship for the plate,
    and tries transaction_type/maintenance_type/purpose/category in that
    order for the label. Returns {"plate_number": None, "type_label": None}
    (both silently absent, never raising) if the table isn't registered
    or the record has nothing matching."""
    result = {"plate_number": None, "type_label": None}
    entry = _REGISTRY.get(reference_table)
    if entry is None:
        return result
    module_path, class_name, _ = entry
    try:
        import importlib
        module = importlib.import_module(module_path)
        model = getattr(module, class_name)
        from app.extensions import db
        record = db.session.get(model, reference_id)
        if record is None:
            return result

        vehicle = getattr(record, "vehicle", None)
        if vehicle is not None:
            result["plate_number"] = (getattr(vehicle, "plate_number", None)
                                     or getattr(vehicle, "conduction_number", None))

        for attr in ("transaction_type", "maintenance_type"):
            related = getattr(record, attr, None)
            if related is not None and getattr(related, "name", None):
                result["type_label"] = related.name
                break
        else:
            for attr in ("purpose", "category", "registration_type"):
                value = getattr(record, attr, None)
                if value:
                    result["type_label"] = str(value).replace("_", " ").title()
                    break

        # A short statement of WHAT is being requested. The type label
        # alone says "Assignment" but not assignment of what, to whom --
        # so an approver still had to open every task to find out. Built
        # generically from whichever of these fields the record actually
        # has, so it works for any transaction module without
        # special-casing.
        parts = []
        driver = getattr(record, "driver", None)
        if driver is not None and getattr(driver, "full_name", None):
            parts.append(f"to {driver.full_name}")
        destination = getattr(record, "destination_branch", None)
        if destination is not None and getattr(destination, "name", None):
            parts.append(f"to {destination.name}")
        recipient = getattr(record, "disposal_recipient", None)
        if recipient:
            parts.append(f"to {recipient}")
        description = getattr(record, "description", None)
        if description:
            text = str(description).strip()
            parts.append(text if len(text) <= 60 else text[:57] + "...")
        result["activity"] = " — ".join(parts) if parts else None
    except Exception:
        pass
    return result
