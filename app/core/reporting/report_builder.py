"""Custom Report Builder — curated data sources and safe query building.

Deliberately NOT a "paste your own SELECT" box. Everything a person can
choose comes from the whitelist below, and the SQL is constructed by
SQLAlchemy from those choices, so:

  * a report can only ever read tables and columns that appear here --
    no reaching into users, password hashes, or the audit trail;
  * values are always bound parameters, so a filter value can never
    alter the query's structure (there is no string concatenation of
    user input into SQL anywhere in this module);
  * each source declares the permission its data already requires, so a
    custom report cannot be used to see data the person is not allowed
    to see through the normal screens; fields drawn from ANOTHER module
    can declare that module's permission too;
  * each source applies the SAME organisational scope as its own list
    screen (v315). A report never shows a row the module's list would
    hide from the same person.

Adding a new source or column is an explicit, reviewable code change
rather than something a user can do by typing a table name.

v315 (2026-11-18) rebuilt the query core around selected column
EXPRESSIONS instead of loading ORM objects and walking attributes in
Python. That is what makes computed fields (names, the approver of a
PR), aliased joins (a vehicle's branch AND a PR's department branch in
one report), totals, grouping and paging possible. Every source key and
field key that existed before is unchanged, so saved reports keep
working; see tests/unit/test_custom_report_builder*.py.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import (String, and_, case, exists, false, func, literal,
                        not_, or_, select, type_coerce)
from sqlalchemy.orm import aliased

from app.extensions import db


def _model(path):
    import importlib
    module_path, class_name = path.rsplit(".", 1)
    return getattr(importlib.import_module(module_path), class_name)


class Field:
    """One selectable column.

    `attr` is a column on the base model ("status") or on a declared
    join ("vehicle.plate_number", where "vehicle" is the JOIN KEY).
    `expr` instead computes the column from the query context (names,
    sub-selects); it is code in this module, never user input.
    """

    def __init__(self, key, label, attr=None, kind="string", join=None, *,
                 category=None, expr=None, aggregate=None, scale=None,
                 permission=None, per_header=False, help=None):
        self.key = key          # stable identifier stored in saved reports
        self.label = label      # what the person sees
        self.attr = attr        # dotted path: column or join_key.column
        self.kind = kind        # string | number | date | datetime | boolean
        if join is None:
            self.joins = ()
        elif isinstance(join, (tuple, list)):
            self.joins = tuple(join)
        else:
            self.joins = (join,)
        self.category = category or "General"
        self.expr = expr
        self.aggregate = aggregate  # "sum" -> totalled in footers/subtotals
        self.scale = scale          # decimal places for display/export
        self.permission = permission
        # A header value (e.g. a PR's amount) that must appear only on
        # the FIRST detail line when detail lines are joined, so totals
        # are not multiplied by the number of lines.
        self.per_header = per_header
        self.help = help

    @property
    def join(self):
        """Kept for callers of the old single-join attribute."""
        return self.joins[0] if self.joins else None


class Join:
    """A pre-declared, allowed relationship. Users pick fields, never
    joins directly -- the builder works out which joins are needed.

    Either `relationship` (an ORM relationship on the parent) or
    `target` + `on(parent, target)` (an explicit condition). `fanout`
    marks a one-to-many join (detail lines): it multiplies rows, so it
    is joined only when one of its columns is SHOWN; a filter on it
    alone becomes an EXISTS test instead.
    """

    def __init__(self, key, relationship=None, label="", *, target=None,
                 on=None, parent=None, fanout=False):
        self.key = key
        self.relationship = relationship
        self.label = label
        self.target = target
        self.on = on
        self.parent = parent
        self.fanout = fanout


class DataSource:
    def __init__(self, key, label, model_path, permission, joins, fields,
                 description="", *, scope=None, categories=None,
                 notes=None):
        self.key = key
        self.label = label
        self.model_path = model_path
        self.permission = permission
        self.description = description
        self.joins = {j.key: j for j in joins}
        self.fields = {f.key: f for f in fields}
        self.scope = scope
        self.categories = categories or []
        self.notes = notes or []

    @property
    def model(self):
        return _model(self.model_path)

    def visible_fields(self, user):
        """Fields this user may use. `user=None` means an internal call
        (CLI, tests) with no per-user restriction."""
        return [f for f in self.fields.values()
                if user is None or not f.permission
                or user.has_permission(f.permission)]

    def category_order(self):
        seen = list(self.categories)
        for f in self.fields.values():
            if f.category not in seen:
                seen.append(f.category)
        return seen


# ── shared SQL helpers (code, never user input) ─────────────────────────

def _s(expr):
    """Treat an expression as a string so `+` compiles to || on SQLite
    and CONCAT() on MySQL."""
    return type_coerce(expr, String)


def _trimmed(expr):
    return func.nullif(func.trim(_s(expr)), "")


def _person_name(entity):
    """First + last name of a User, falling back to the username."""
    full = (_s(func.coalesce(entity.first_name, "")) + literal(" ")
            + _s(func.coalesce(entity.last_name, "")))
    return func.coalesce(_trimmed(full), entity.username)


def _driver_name(entity):
    """SQL twin of Driver.full_name: First M. Last Suffix."""
    middle = case((func.coalesce(entity.middle_name, "") != "",
                   _s(func.substr(entity.middle_name, 1, 1)) + literal(". ")),
                  else_=literal(""))
    suffix = case((func.coalesce(entity.suffix, "") != "",
                   literal(" ") + _s(entity.suffix)), else_=literal(""))
    full = (_s(func.coalesce(entity.first_name, "")) + literal(" ")
            + _s(middle) + _s(func.coalesce(entity.last_name, ""))
            + _s(suffix))
    return _trimmed(full)


class _Ctx:
    """What a field expression can see: the base model and the aliases
    of the joins applied to this query."""

    def __init__(self, source):
        self.source = source
        self.base = source.model
        self.aliases = {}

    def e(self, key):
        try:
            return self.aliases[key]
        except KeyError:
            raise ReportBuilderError(
                f"Internal: join '{key}' was not applied.") from None


def _vehicle_scope(query, ctx, user):
    """Same predicate as VehicleService.list_page()."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    clause = UserOrgScopeService().sql_visibility_clause(user.id, Vehicle)
    if clause is not None:
        query = query.filter(clause)
    if getattr(user, "restrict_to_assigned_vehicles", False):
        from app.modules.user_management.assignee_scope_service import (
            AssigneeScopeService)
        ids = AssigneeScopeService().assigned_vehicle_ids(user)
        query = query.filter(Vehicle.id.in_(ids) if ids else false())
    return query


def _transaction_scope(service_path):
    """The module's own list-screen visibility filter (requester, comment
    participants, branch scope) -- one rule, shared."""
    def apply(query, ctx, user):
        return _model(service_path)()._apply_visibility_filter(query, user)
    return apply


def _fuel_scope(query, ctx, user):
    """Same rule as the Fuel list (fuel_scope_ids): through the vehicle's
    branch."""
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.fuel.analytics import fuel_scope_ids
    ids = fuel_scope_ids(user)
    if ids is None:
        return query
    return query.filter(ctx.base.vehicle_id.in_(
        select(Vehicle.id).where(Vehicle.branch_id.in_(ids))))


# ── model paths ──────────────────────────────────────────────────────────
VEHICLE = "app.modules.master_data.vehicle.models.Vehicle"
BRANCH = "app.modules.master_data.org.models.Branch"
DEPARTMENT = "app.modules.master_data.org.models.Department"
COMPANY = "app.modules.master_data.org.models.OrgCompany"
DRIVER = "app.modules.master_data.driver.models.Driver"
VENDOR = "app.modules.master_data.vendor.models.Vendor"
VEHICLE_TYPE = "app.modules.master_data.reference.models.VehicleType"
USER = "app.modules.user_management.models.User"
APPROVAL_INSTANCE = "app.core.approval.models.ApprovalInstance"
APPROVAL_ACTION = "app.core.approval.models.ApprovalAction"
FUEL_TXN = "app.modules.transactions.fuel.models.FuelTransaction"
FUEL_CARD = "app.modules.transactions.fuel.models.FuelCard"
MO = "app.modules.transactions.maintenance_order.models.MaintenanceOrder"
PR = "app.modules.transactions.purchase_request.models.PurchaseRequest"
PR_LINE = "app.modules.transactions.purchase_request.models.PurchaseRequestLine"


def _fk(column):
    """Join helper: parent.<column> == target.id."""
    return lambda parent, target: getattr(parent, column) == target.id


def _vehicle_org_joins(prefix="vehicle"):
    """Branch, company and type of an already-joined vehicle."""
    return [
        Join(f"{prefix}_branch", target=BRANCH, on=_fk("branch_id"),
             parent=prefix, label="Vehicle branch"),
        Join(f"{prefix}_company", target=COMPANY, on=_fk("company_id"),
             parent=prefix, label="Vehicle company"),
        Join(f"{prefix}_type", target=VEHICLE_TYPE, on=_fk("vehicle_type_id"),
             parent=prefix, label="Vehicle type"),
    ]


def _approval_actor(ctx, instance_col, value):
    """Who did the latest APPROVE on the document's approval instance,
    or when (value="when")."""
    action = aliased(_model(APPROVAL_ACTION))
    if value == "when":
        target = action.acted_at
        q = select(target)
    else:
        user = aliased(_model(USER))
        q = (select(_person_name(user)).select_from(action)
             .join(user, user.id == action.acted_by))
    return (q.where(action.instance_id == instance_col,
                    action.action == "APPROVE")
            .order_by(action.acted_at.desc(), action.id.desc())
            .limit(1).scalar_subquery())


def _pr_line_stat(ctx, what):
    line = aliased(_model(PR_LINE))
    agg = func.count(line.id) if what == "count" else func.sum(line.line_total)
    return (select(agg).where(line.pr_id == ctx.base.id)
            .scalar_subquery())


DATA_SOURCES = {
    "vehicles": DataSource(
        key="vehicles", label="Vehicles",
        model_path=VEHICLE,
        permission="vehicle.view",
        description="Fleet master data, with branch and vehicle type.",
        scope=_vehicle_scope,
        categories=["Identification", "Specification", "Status",
                    "Registration", "Assignment", "Organization",
                    "Cost", "Audit"],
        joins=[
            Join("branch", "branch", "Branch"),
            Join("vehicle_type", "vehicle_type", "Vehicle Type"),
            Join("assigned_driver", "assigned_driver", "Assigned Driver"),
            Join("company", target=COMPANY, on=_fk("company_id"),
                 label="Company"),
        ],
        fields=[
            Field("plate_number", "Plate No.", "plate_number",
                  category="Identification"),
            Field("conduction_number", "Conduction No.", "conduction_number",
                  category="Identification"),
            Field("brand", "Brand", "brand", category="Specification"),
            Field("model", "Model", "model", category="Specification"),
            Field("year", "Year", "year", "number", category="Specification"),
            Field("variant", "Variant", "variant", category="Specification"),
            Field("color", "Colour", "color", category="Specification"),
            Field("engine_number", "Engine No.", "engine_number",
                  category="Identification"),
            Field("chassis_number", "Chassis No.", "chassis_number",
                  category="Identification"),
            Field("fuel_type", "Fuel Type", "fuel_type",
                  category="Specification"),
            Field("status", "Status", "status", category="Status"),
            Field("current_odometer", "Odometer", "current_odometer",
                  "number", category="Status"),
            Field("acquisition_date", "Acquisition Date", "acquisition_date",
                  "date", category="Cost"),
            Field("acquisition_cost", "Acquisition Cost", "acquisition_cost",
                  "number", category="Cost", aggregate="sum", scale=2),
            Field("far_number", "FAR No.", "far_number",
                  category="Identification"),
            Field("branch_name", "Branch", "branch.name", join="branch",
                  category="Organization"),
            Field("vehicle_type_name", "Vehicle Type", "vehicle_type.name",
                  join="vehicle_type", category="Specification"),
            Field("driver_name", "Assigned Driver", join="assigned_driver",
                  expr=lambda c: _driver_name(c.e("assigned_driver")),
                  category="Assignment"),
            # v315 additions
            Field("transmission", "Transmission", "transmission",
                  category="Specification"),
            Field("branch_code", "Branch Code", "branch.code", join="branch",
                  category="Organization"),
            Field("company_name", "Company", "company.short_name",
                  join="company", category="Organization"),
            Field("or_number", "OR No.", "or_number", category="Registration"),
            Field("cr_number", "CR No.", "cr_number", category="Registration"),
            Field("registration_expiry", "Registration Expiry",
                  "last_known_registration_expiry", "date",
                  category="Registration"),
            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
        ]),
    "maintenance_orders": DataSource(
        key="maintenance_orders", label="Maintenance Orders",
        model_path=MO,
        permission="maintenanceorder.view",
        description="Work orders, with vehicle, maintenance type and vendor.",
        scope=_transaction_scope(
            "app.modules.transactions.maintenance_order.service"
            ".MaintenanceOrderService"),
        categories=["Order", "Vehicle", "Organization", "Supplier", "Cost",
                    "Audit"],
        joins=[
            Join("vehicle", "vehicle", "Vehicle"),
            Join("maintenance_type", "maintenance_type", "Maintenance Type"),
            Join("transaction_type", "transaction_type", "Transaction Type"),
            Join("vendor", "vendor", "Vendor"),
            *_vehicle_org_joins("vehicle"),
        ],
        fields=[
            Field("document_number", "MO No.", "document_number",
                  category="Order"),
            Field("status", "Status", "status", category="Order"),
            Field("order_category", "Category", "order_category",
                  category="Order"),
            Field("scheduled_date", "Scheduled", "scheduled_date", "date",
                  category="Order"),
            Field("completed_date", "Completed", "completed_date", "date",
                  category="Order"),
            Field("odometer_at_service", "Odometer", "odometer_at_service",
                  "number", category="Vehicle"),
            Field("estimated_cost", "Est. Cost", "estimated_cost", "number",
                  category="Cost", aggregate="sum", scale=2),
            Field("actual_cost", "Actual Cost", "actual_cost", "number",
                  category="Cost", aggregate="sum", scale=2),
            Field("assigned_mechanic", "Mechanic", "assigned_mechanic",
                  category="Order"),
            Field("description", "Description", "description",
                  category="Order"),
            Field("plate_number", "Plate No.", "vehicle.plate_number",
                  join="vehicle", category="Vehicle"),
            Field("vehicle_brand", "Brand", "vehicle.brand", join="vehicle",
                  category="Vehicle"),
            Field("vehicle_model", "Model", "vehicle.model", join="vehicle",
                  category="Vehicle"),
            Field("maintenance_type_name", "Maintenance Type",
                  "maintenance_type.name", join="maintenance_type",
                  category="Order"),
            Field("transaction_type_name", "Transaction Type",
                  "transaction_type.name", join="transaction_type",
                  category="Order"),
            Field("vendor_name", "Vendor", "vendor.name", join="vendor",
                  category="Supplier"),
            # v315 additions
            Field("branch_name", "Branch", "vehicle_branch.name",
                  join="vehicle_branch", category="Organization"),
            Field("company_name", "Company", "vehicle_company.short_name",
                  join="vehicle_company", category="Organization"),
            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
        ]),
    "vehicle_registrations": DataSource(
        key="vehicle_registrations", label="Vehicle Registrations",
        model_path="app.modules.transactions.vehicle_registration.models"
                   ".VehicleRegistration",
        permission="vehicleregistration.view",
        description="LTO registration records with expiry dates.",
        scope=_transaction_scope(
            "app.modules.transactions.vehicle_registration.service"
            ".VehicleRegistrationService"),
        categories=["Registration", "Vehicle", "Organization", "Cost",
                    "Audit"],
        joins=[Join("vehicle", "vehicle", "Vehicle"),
               *_vehicle_org_joins("vehicle")],
        fields=[
            Field("document_number", "Registration No.", "document_number",
                  category="Registration"),
            Field("status", "Status", "status", category="Registration"),
            Field("or_number", "OR No.", "or_number", category="Registration"),
            Field("cr_number", "CR No.", "cr_number", category="Registration"),
            Field("expiry_date", "Expiry Date", "expiry_date", "date",
                  category="Registration"),
            Field("plate_number", "Plate No.", "vehicle.plate_number",
                  join="vehicle", category="Vehicle"),
            Field("vehicle_brand", "Brand", "vehicle.brand", join="vehicle",
                  category="Vehicle"),
            # v315 additions
            Field("registration_type", "Registration Type",
                  "registration_type", category="Registration"),
            Field("registration_date", "Registration Date",
                  "registration_date", "date", category="Registration"),
            Field("validity_years", "Validity (years)", "validity_years",
                  "number", category="Registration"),
            Field("or_cr_cost", "OR/CR Cost", "or_cr_cost", "number",
                  category="Cost", aggregate="sum", scale=2),
            Field("odometer_at_registration", "Odometer at Registration",
                  "odometer_at_registration", "number", category="Vehicle"),
            Field("vehicle_model", "Model", "vehicle.model", join="vehicle",
                  category="Vehicle"),
            Field("branch_name", "Branch", "vehicle_branch.name",
                  join="vehicle_branch", category="Organization"),
            Field("company_name", "Company", "vehicle_company.short_name",
                  join="vehicle_company", category="Organization"),
            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
        ]),
    "trip_tickets": DataSource(
        key="trip_tickets", label="Trip Tickets",
        model_path="app.modules.transactions.trip_ticket.models.TripTicket",
        permission="tripticket.view",
        description="Vehicle trips with destination and odometer readings.",
        scope=_transaction_scope(
            "app.modules.transactions.trip_ticket.service"
            ".TripTicketService"),
        categories=["Trip", "Vehicle", "Driver", "Organization", "Audit"],
        joins=[Join("vehicle", "vehicle", "Vehicle"),
               Join("driver", target=DRIVER, on=_fk("driver_id"),
                    label="Driver"),
               *_vehicle_org_joins("vehicle")],
        fields=[
            Field("document_number", "Trip No.", "document_number",
                  category="Trip"),
            Field("status", "Status", "status", category="Trip"),
            # The key stays "trip_date" (saved reports use it), but the
            # model has no such column: it never showed a value. It is
            # the departure date and time.
            Field("trip_date", "Departure", "departure_datetime", "datetime",
                  category="Trip"),
            Field("destination", "Destination", "destination",
                  category="Trip"),
            Field("purpose", "Purpose", "purpose", category="Trip"),
            Field("odometer_out", "Odometer Out", "odometer_out", "number",
                  category="Trip"),
            Field("odometer_in", "Odometer In", "odometer_in", "number",
                  category="Trip"),
            Field("plate_number", "Plate No.", "vehicle.plate_number",
                  join="vehicle", category="Vehicle"),
            # v315 additions
            Field("return_datetime", "Return", "return_datetime", "datetime",
                  category="Trip"),
            Field("distance_km", "Distance (km)", kind="number",
                  expr=lambda c: c.base.odometer_in - c.base.odometer_out,
                  category="Trip", aggregate="sum",
                  help="Odometer in minus odometer out; empty until the "
                       "vehicle is back."),
            Field("driver_name", "Driver", join="driver",
                  expr=lambda c: func.coalesce(
                      _driver_name(c.e("driver")),
                      _trimmed(c.base.driver_name_manual)),
                  category="Driver"),
            Field("vehicle_brand", "Brand", "vehicle.brand", join="vehicle",
                  category="Vehicle"),
            Field("vehicle_model", "Model", "vehicle.model", join="vehicle",
                  category="Vehicle"),
            Field("branch_name", "Branch", "vehicle_branch.name",
                  join="vehicle_branch", category="Organization"),
            Field("company_name", "Company", "vehicle_company.short_name",
                  join="vehicle_company", category="Organization"),
            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
        ]),
    "fuel_transactions": DataSource(
        key="fuel_transactions", label="Fuel Transactions",
        model_path=FUEL_TXN,
        permission="fuel.view",
        description="Every fuel fill: litres, price, amount, station and "
                    "the validated odometer, with vehicle, driver and "
                    "branch.",
        scope=_fuel_scope,
        categories=["Transaction", "Vehicle", "Driver", "Fuel",
                    "Odometer & Efficiency", "Supplier / Station", "Cost",
                    "Organization", "Audit"],
        notes=["Fuel transactions have no approval status. Use Review "
               "status (Needs review / Odometer status) instead."],
        joins=[
            Join("vehicle", target=VEHICLE, on=_fk("vehicle_id"),
                 label="Vehicle"),
            *_vehicle_org_joins("vehicle"),
            Join("driver", target=DRIVER, on=_fk("driver_id"),
                 label="Driver"),
            Join("vendor", target=VENDOR, on=_fk("vendor_id"),
                 label="Supplier"),
            Join("fuel_card", target=FUEL_CARD, on=_fk("fuel_card_id"),
                 label="Fuel card"),
            Join("imported_by_user", target=USER, on=_fk("imported_by"),
                 label="Imported by"),
            Join("created_by_user", target=USER, on=_fk("created_by"),
                 label="Created by"),
        ],
        fields=[
            Field("document_number", "Fuel Transaction No.",
                  "document_number", category="Transaction"),
            Field("transaction_date", "Transaction Date", "transaction_date",
                  "datetime", category="Transaction"),
            Field("reference_number", "Reference No.", "reference_number",
                  category="Transaction"),
            Field("source", "Source", "source", category="Transaction",
                  help="MANUAL or IMPORT"),
            Field("remarks", "Remarks", "remarks", category="Transaction"),
            Field("needs_review", "Needs Review", "needs_review", "boolean",
                  category="Transaction"),
            Field("anomaly_flags", "Anomaly Flags", "anomaly_flags",
                  category="Transaction"),

            Field("plate_number", "Plate No.", "vehicle.plate_number",
                  join="vehicle", category="Vehicle"),
            Field("conduction_number", "Conduction No.",
                  "vehicle.conduction_number", join="vehicle",
                  category="Vehicle"),
            Field("vehicle", "Vehicle", join="vehicle", category="Vehicle",
                  expr=lambda c: _trimmed(
                      _s(func.coalesce(c.e("vehicle").brand, ""))
                      + literal(" ")
                      + _s(func.coalesce(c.e("vehicle").model, "")))),
            Field("vehicle_brand", "Brand", "vehicle.brand", join="vehicle",
                  category="Vehicle"),
            Field("vehicle_model", "Model", "vehicle.model", join="vehicle",
                  category="Vehicle"),
            Field("vehicle_type", "Vehicle Type", "vehicle_type.name",
                  join="vehicle_type", category="Vehicle"),
            Field("vehicle_status", "Vehicle Status", "vehicle.status",
                  join="vehicle", category="Vehicle"),
            Field("vehicle_fuel_type", "Vehicle Fuel Type",
                  "vehicle.fuel_type", join="vehicle", category="Vehicle"),

            Field("driver_name", "Driver", join="driver",
                  expr=lambda c: _driver_name(c.e("driver")),
                  category="Driver"),
            Field("driver_employee_number", "Driver Employee No.",
                  "driver.employee_number", join="driver",
                  category="Driver", permission="driver.view"),
            Field("driver_position", "Driver Position", "driver.position",
                  join="driver", category="Driver", permission="driver.view"),

            Field("fuel_type", "Fuel Type", "fuel_type", category="Fuel"),
            Field("litres", "Quantity (L)", "litres", "number",
                  category="Fuel", aggregate="sum", scale=3),
            Field("price_per_litre", "Unit Price", "price_per_litre",
                  "number", category="Cost", scale=4),
            Field("total_amount", "Total Amount", "total_amount", "number",
                  category="Cost", aggregate="sum", scale=2),
            Field("cost_per_km", "Cost per km", "cost_per_km", "number",
                  category="Cost", scale=4),

            Field("odometer_reported", "Odometer (reported)",
                  "odometer_reported", "number",
                  category="Odometer & Efficiency"),
            Field("odometer_used", "Odometer (validated)", "odometer_used",
                  "number", category="Odometer & Efficiency"),
            Field("odometer_status", "Odometer Status", "odometer_status",
                  category="Odometer & Efficiency",
                  help="OK, SUSPECT, CORRECTED or MISSING"),
            Field("odometer_confirmed", "Odometer Confirmed",
                  "odometer_confirmed", "boolean",
                  category="Odometer & Efficiency"),
            Field("odometer_note", "Odometer Note", "odometer_note",
                  category="Odometer & Efficiency"),
            Field("distance_km", "Distance (km)", "distance_km", "number",
                  category="Odometer & Efficiency", aggregate="sum"),
            Field("km_per_litre", "km per Litre", "km_per_litre", "number",
                  category="Odometer & Efficiency", scale=3),

            Field("station", "Station", "station",
                  category="Supplier / Station"),
            Field("supplier_name", "Supplier", "vendor.name", join="vendor",
                  category="Supplier / Station"),
            Field("supplier_code", "Supplier Code", "vendor.code",
                  join="vendor", category="Supplier / Station"),
            Field("fuel_card_number", "Fuel Card No.",
                  "fuel_card.card_number", join="fuel_card",
                  category="Supplier / Station"),
            Field("fuel_card_provider", "Card Provider",
                  "fuel_card.provider", join="fuel_card",
                  category="Supplier / Station"),

            Field("branch_name", "Branch", "vehicle_branch.name",
                  join="vehicle_branch", category="Organization"),
            Field("branch_code", "Branch Code", "vehicle_branch.code",
                  join="vehicle_branch", category="Organization"),
            Field("company_name", "Company", "vehicle_company.short_name",
                  join="vehicle_company", category="Organization"),

            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
            Field("created_by", "Created By", join="created_by_user",
                  expr=lambda c: _person_name(c.e("created_by_user")),
                  category="Audit"),
            Field("import_filename", "Import File", "import_filename",
                  category="Audit"),
            Field("import_batch_id", "Import Batch", "import_batch_id",
                  category="Audit"),
            Field("imported_at", "Imported At", "imported_at", "datetime",
                  category="Audit"),
            Field("imported_by", "Imported By", join="imported_by_user",
                  expr=lambda c: _person_name(c.e("imported_by_user")),
                  category="Audit"),
        ]),
    "purchase_requisitions": DataSource(
        key="purchase_requisitions", label="Purchase Requisitions (PR)",
        model_path=PR,
        permission="purchaserequest.view",
        description="PR headers with their item lines, requester, "
                    "department, supplier, the Work Order they came from "
                    "and the approval trail.",
        scope=_transaction_scope(
            "app.modules.transactions.purchase_request.service"
            ".PurchaseRequestService"),
        categories=["PR Header", "Requester", "Organization",
                    "Vehicle & Work Order", "Item Details", "Supplier",
                    "Approval", "Financial", "Audit"],
        notes=["A PR is one header with several item lines. Choosing any "
               "Item Details column gives one row per item; the PR amount "
               "is then shown on the first item only, so totals are not "
               "counted once per line.",
               "PR Date is the date the PR was created; PRs have no "
               "separate request-date field."],
        joins=[
            Join("lines", target=PR_LINE,
                 on=lambda parent, t: t.pr_id == parent.id,
                 fanout=True, label="Item lines"),
            Join("requester", target=USER, on=_fk("requested_by"),
                 label="Requester"),
            Join("department", target=DEPARTMENT, on=_fk("department_id"),
                 label="Department"),
            Join("department_branch", target=BRANCH, on=_fk("branch_id"),
                 parent="department", label="Branch"),
            Join("department_company", target=COMPANY,
                 on=_fk("company_id"), parent="department", label="Company"),
            Join("vendor", target=VENDOR, on=_fk("vendor_id"),
                 label="Supplier"),
            Join("approval", target=APPROVAL_INSTANCE,
                 on=_fk("approval_instance_id"), label="Approval"),
            Join("work_order", target=MO,
                 on=lambda parent, t: t.id == _first_mo_id_for(parent),
                 label="Work Order"),
            Join("wo_vehicle", target=VEHICLE, on=_fk("vehicle_id"),
                 parent="work_order", label="Work Order vehicle"),
            Join("created_by_user", target=USER, on=_fk("created_by"),
                 label="Created by"),
        ],
        fields=[
            Field("document_number", "PR No.", "document_number",
                  category="PR Header"),
            Field("pr_date", "PR Date", "created_at", "datetime",
                  category="PR Header"),
            Field("needed_by_date", "Required Date", "needed_by_date",
                  "date", category="PR Header"),
            Field("description", "Description", "description",
                  category="PR Header"),
            Field("justification", "Justification", "justification",
                  category="PR Header"),
            Field("status", "Status", "status", category="PR Header"),

            Field("requester_name", "Requester", join="requester",
                  expr=lambda c: _person_name(c.e("requester")),
                  category="Requester"),
            Field("requester_username", "Requester Username",
                  "requester.username", join="requester",
                  category="Requester"),
            Field("requester_employee_id", "Requester Employee ID",
                  "requester.employee_id", join="requester",
                  category="Requester"),

            Field("department_name", "Department", "department.name",
                  join="department", category="Organization"),
            Field("branch_name", "Branch", "department_branch.name",
                  join="department_branch", category="Organization",
                  help="The requesting department's branch."),
            Field("branch_code", "Branch Code", "department_branch.code",
                  join="department_branch", category="Organization"),
            Field("company_name", "Company",
                  "department_company.short_name", join="department_company",
                  category="Organization"),

            Field("work_order_number", "Work Order (MO) No.",
                  "work_order.document_number", join="work_order",
                  category="Vehicle & Work Order",
                  permission="maintenanceorder.view"),
            Field("work_order_status", "Work Order Status",
                  "work_order.status", join="work_order",
                  category="Vehicle & Work Order",
                  permission="maintenanceorder.view"),
            Field("plate_number", "Plate No.", "wo_vehicle.plate_number",
                  join="wo_vehicle", category="Vehicle & Work Order",
                  permission="maintenanceorder.view"),
            Field("vehicle", "Vehicle", join="wo_vehicle",
                  category="Vehicle & Work Order",
                  permission="maintenanceorder.view",
                  expr=lambda c: _trimmed(
                      _s(func.coalesce(c.e("wo_vehicle").brand, ""))
                      + literal(" ")
                      + _s(func.coalesce(c.e("wo_vehicle").model, "")))),

            Field("item_description", "Item Description",
                  "lines.item_description", join="lines",
                  category="Item Details"),
            Field("quantity", "Quantity", "lines.quantity", "number",
                  join="lines", category="Item Details", aggregate="sum",
                  scale=2),
            Field("unit_cost", "Unit Cost", "lines.unit_cost", "number",
                  join="lines", category="Item Details", scale=2),
            Field("line_total", "Line Total", "lines.line_total", "number",
                  join="lines", category="Item Details", aggregate="sum",
                  scale=2),

            Field("supplier_name", "Supplier", "vendor.name", join="vendor",
                  category="Supplier"),
            Field("supplier_code", "Supplier Code", "vendor.code",
                  join="vendor", category="Supplier"),
            Field("supplier_type", "Supplier Type", "vendor.vendor_type",
                  join="vendor", category="Supplier"),
            Field("supplier_tin", "Supplier TIN", "vendor.tin",
                  join="vendor", category="Supplier",
                  permission="vendor.view"),

            Field("approval_status", "Approval Status", "approval.status",
                  join="approval", category="Approval"),
            Field("approval_level", "Current Approval Level",
                  "approval.current_level", "number", join="approval",
                  category="Approval"),
            Field("approver", "Approved By", category="Approval",
                  expr=lambda c: _approval_actor(
                      c, c.base.approval_instance_id, "who"),
                  help="Who gave the latest approval."),
            Field("approval_date", "Approval Date", kind="datetime",
                  category="Approval",
                  expr=lambda c: _approval_actor(
                      c, c.base.approval_instance_id, "when")),

            Field("amount", "PR Amount", "amount", "number",
                  category="Financial", aggregate="sum", scale=2,
                  per_header=True,
                  help="The PR total. With item lines shown it appears on "
                       "the first item only."),
            Field("line_count", "No. of Items", kind="number",
                  expr=lambda c: _pr_line_stat(c, "count"),
                  category="Financial", aggregate="sum", per_header=True),
            Field("lines_total", "Items Total", kind="number",
                  expr=lambda c: _pr_line_stat(c, "sum"),
                  category="Financial", aggregate="sum", scale=2,
                  per_header=True),

            Field("created_at", "Created", "created_at", "datetime",
                  category="Audit"),
            Field("updated_at", "Last Updated", "updated_at", "datetime",
                  category="Audit"),
            Field("created_by", "Created By", join="created_by_user",
                  expr=lambda c: _person_name(c.e("created_by_user")),
                  category="Audit"),
        ]),
}


def _first_mo_id_for(pr_entity):
    mo = aliased(_model(MO))
    return (select(func.min(mo.id))
            .where(mo.purchase_request_id == pr_entity.id)
            .scalar_subquery())


# ── operators ────────────────────────────────────────────────────────────

# The ORIGINAL operator set, kept as-is for the Flask form, which offers
# every operator for every field and has a single value box.
OPERATORS = {
    "eq": "equals",
    "ne": "does not equal",
    "contains": "contains",
    "gt": "greater than",
    "gte": "greater than or equal to",
    "lt": "less than",
    "lte": "less than or equal to",
    "is_empty": "is empty",
    "not_empty": "is not empty",
}

OPERATOR_LABELS = {
    **OPERATORS,
    "not_contains": "does not contain",
    "between": "between",
}

NO_VALUE_OPS = ("is_empty", "not_empty")

OPERATORS_BY_KIND = {
    "string": ["eq", "ne", "contains", "not_contains", "is_empty",
               "not_empty"],
    "number": ["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_empty",
               "not_empty"],
    "date": ["eq", "ne", "gt", "gte", "lt", "lte", "between", "is_empty",
             "not_empty"],
    "datetime": ["eq", "ne", "gt", "gte", "lt", "lte", "between",
                 "is_empty", "not_empty"],
    "boolean": ["eq", "is_empty", "not_empty"],
}

MAX_ROWS = 5000


class ReportBuilderError(Exception):
    pass


def _coerce(value, kind, label=None):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    name = f" for {label}" if label else ""
    if kind == "number":
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            raise ReportBuilderError(
                f"'{value}' is not a number{name}.") from None
    if kind in ("date", "datetime"):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()[:10]
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        raise ReportBuilderError(
            f"'{value}' is not a valid date{name} (use YYYY-MM-DD).")
    if kind == "boolean":
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y"):
            return True
        if text in ("0", "false", "no", "n"):
            return False
        raise ReportBuilderError(f"'{value}' is not yes/no{name}.")
    return str(value)


def _day(d):
    return datetime.combine(d, time.min)


def _condition(column, kind, op, value, value2):
    if op == "is_empty":
        return (or_(column.is_(None), column == "") if kind == "string"
                else column.is_(None))
    if op == "not_empty":
        return (and_(column.isnot(None), column != "") if kind == "string"
                else column.isnot(None))
    if op == "contains":
        return column.ilike(f"%{_escape_like(value)}%", escape="\\")
    if op == "not_contains":
        return or_(column.is_(None),
                   not_(column.ilike(f"%{_escape_like(value)}%",
                                     escape="\\")))
    if kind == "datetime":
        # A date typed against a date-AND-time column means the whole day.
        start, end = _day(value), _day(value) + timedelta(days=1)
        if op == "eq":
            return and_(column >= start, column < end)
        if op == "ne":
            return or_(column < start, column >= end)
        if op == "gt":
            return column >= end
        if op == "gte":
            return column >= start
        if op == "lt":
            return column < start
        if op == "lte":
            return column < end
        if op == "between":
            return and_(column >= start,
                        column < _day(value2) + timedelta(days=1))
    if op == "eq":
        return column == value
    if op == "ne":
        return column != value
    if op == "gt":
        return column > value
    if op == "gte":
        return column >= value
    if op == "lt":
        return column < value
    if op == "lte":
        return column <= value
    if op == "between":
        return column.between(value, value2)
    raise ReportBuilderError(f"Unknown operator '{op}'.")


def _escape_like(value):
    return (str(value).replace("\\", "\\\\").replace("%", "\\%")
            .replace("_", "\\_"))


# ── query construction ──────────────────────────────────────────────────

def _join_chain(source, key):
    """[key's ancestors..., key] in join order."""
    chain = []
    while key:
        join = source.joins.get(key)
        if join is None:
            raise ReportBuilderError(f"Unknown join '{key}'.")
        chain.insert(0, join)
        key = join.parent
    return chain


def _apply_join(query, ctx, join):
    if join.key in ctx.aliases:
        return query
    parent = ctx.aliases[join.parent] if join.parent else ctx.base
    if join.relationship:
        rel = getattr(parent, join.relationship)
        alias = aliased(rel.property.mapper.class_)
        query = query.outerjoin(alias, rel.of_type(alias))
    else:
        alias = aliased(_model(join.target))
        query = query.outerjoin(alias, join.on(parent, alias))
    ctx.aliases[join.key] = alias
    return query


def _expr(ctx, field):
    if field.expr is not None:
        return field.expr(ctx)
    if "." not in field.attr:
        return getattr(ctx.base, field.attr)
    join_key, column = field.attr.split(".", 1)
    return getattr(ctx.e(join_key), column)


def _check_access(source, fields, user):
    if user is None:
        return
    for f in fields:
        if f.permission and not user.has_permission(f.permission):
            raise ReportBuilderError(
                f"You do not have access to the field '{f.label}' "
                f"(requires '{f.permission}').")


def _get_field(source, key, what):
    fld = source.fields.get(key)
    if fld is None:
        raise ReportBuilderError(
            f"{what} '{key}' is not available in {source.label}.")
    return fld


def _normalise_filters(source, filters, strict):
    """Validated (field, op, value, value2) tuples.

    strict=True is the builder's preview/save path: every problem is an
    error the person can fix. strict=False is a SAVED report being run:
    rows left half-filled by the older Flask form (a field with no
    value) are skipped as they always were, but nothing invalid is ever
    applied."""
    out = []
    for raw in filters or []:
        key = (raw or {}).get("field")
        if not key:
            continue
        fld = _get_field(source, key, "Filter field")
        op = raw.get("op") or "eq"
        if op not in OPERATOR_LABELS:
            raise ReportBuilderError(f"Unknown operator '{op}'.")
        if strict and op not in OPERATORS_BY_KIND.get(fld.kind, []):
            raise ReportBuilderError(
                f"'{OPERATOR_LABELS[op]}' cannot be used with "
                f"{fld.label}.")
        if op in NO_VALUE_OPS:
            out.append((fld, op, None, None))
            continue
        kind = fld.kind
        if op in ("contains", "not_contains"):
            kind = "string"   # legacy: contains on any column
        value = _coerce(raw.get("value"), kind, fld.label)
        value2 = (_coerce(raw.get("value2"), kind, fld.label)
                  if op == "between" else None)
        if value is None or (op == "between" and value2 is None):
            if strict:
                raise ReportBuilderError(
                    f"Enter {'both values' if op == 'between' else 'a value'}"
                    f" for the {fld.label} filter.")
            continue
        if op == "between" and value2 < value:
            value, value2 = value2, value
        out.append((fld, op, value, value2))
    return out


class _Plan:
    """A built query plus what is needed to read its results."""

    def __init__(self, source, fields, query, ctx, order, group_field,
                 group_index):
        self.source = source
        self.fields = fields
        self.query = query
        self.ctx = ctx
        self.order = order
        self.group_field = group_field
        self.group_index = group_index


def _build(source, fields, filters, filter_logic, sort_field, sort_dir,
           group_field, user):
    ctx = _Ctx(source)
    model = ctx.base

    shown = list(fields)
    for extra in (sort_field, group_field):
        if extra is not None:
            shown.append(extra)
    # Detail-line joins whose columns are actually shown (the row grain
    # becomes one row per line only then).
    fanout_shown = set()
    for f in shown:
        for key in f.joins:
            root = _join_chain(source, key)[0]
            if root.fanout:
                fanout_shown.add(root.key)

    needed = []
    for f in shown + [flt[0] for flt in filters]:
        for key in f.joins:
            chain = _join_chain(source, key)
            if chain[0].fanout and chain[0].key not in fanout_shown:
                continue  # filter-only on detail lines -> EXISTS below
            for j in chain:
                if j not in needed:
                    needed.append(j)

    query = db.session.query(literal(1)).select_from(model)
    for join in needed:
        query = _apply_join(query, ctx, join)

    def column_for(f):
        col = _expr(ctx, f)
        if f.per_header and fanout_shown:
            # Only the first detail line carries a header value.
            child_key = next(iter(fanout_shown))
            child = ctx.aliases[child_key]
            first = aliased(_model(source.joins[child_key].target))
            first_id = (select(func.min(first.id))
                        .where(source.joins[child_key].on(model, first))
                        .scalar_subquery())
            col = case((or_(child.id.is_(None), child.id == first_id), col),
                       else_=None)
        return col

    columns = [column_for(f).label(f"c{i}") for i, f in enumerate(fields)]
    group_index = None
    if group_field is not None:
        columns.append(column_for(group_field).label("g"))
        group_index = len(columns) - 1
    query = query.with_entities(*columns)

    conditions = []
    for fld, op, value, value2 in filters:
        chain = [_join_chain(source, k) for k in fld.joins]
        fanout_join = next((c[0] for c in chain
                            if c[0].fanout and c[0].key not in fanout_shown),
                           None)
        if fanout_join is None:
            conditions.append(
                _condition(_expr(ctx, fld), fld.kind, op, value, value2))
            continue
        child = aliased(_model(fanout_join.target))
        sub = _Ctx(source)
        sub.aliases = dict(ctx.aliases)
        sub.aliases[fanout_join.key] = child
        conditions.append(exists(
            select(literal(1)).select_from(child)
            .where(fanout_join.on(model, child),
                   _condition(_expr(sub, fld), fld.kind, op, value,
                              value2))))
    if conditions:
        joiner = or_ if (filter_logic or "AND").upper() == "OR" else and_
        query = query.filter(joiner(*conditions))

    if user is not None and source.scope is not None:
        query = source.scope(query, ctx, user)

    order = []
    if group_field is not None:
        order.append(_expr(ctx, group_field).asc())
    if sort_field is not None:
        col = _expr(ctx, sort_field)
        order.append(col.desc() if sort_dir == "desc" else col.asc())
    order.append(model.id.asc())
    if fanout_shown:
        order.append(ctx.aliases[next(iter(fanout_shown))].id.asc())
    return _Plan(source, fields, query, ctx, order, group_field, group_index)


def run_report(source_key, field_keys, filters=None, sort_key=None,
               sort_dir="asc", limit=1000, user=None, *, filter_logic="AND",
               group_by=None, page=None, per_page=None, strict=False):
    """Execute a built report definition.

    Returns {columns, rows, row_count, truncated} as before, plus
    total (matching rows before the row limit), page/per_page when
    paged, totals (sums of totalled columns over the whole report) and,
    when grouped, group + groups + group_values.

    `user` is checked against the source's permission, each field's
    permission and the source's organisational scope -- a saved report
    can never become a way around the normal access rules.
    """
    source = DATA_SOURCES.get(source_key)
    if source is None:
        raise ReportBuilderError(f"Unknown data source '{source_key}'.")
    if user is not None and not user.has_permission(source.permission):
        raise ReportBuilderError(
            f"You do not have permission to read {source.label} "
            f"(requires '{source.permission}').")

    keys = []
    for k in (field_keys or []):
        if k in source.fields and k not in keys:
            keys.append(k)
        elif strict and k not in source.fields:
            raise ReportBuilderError(
                f"Column '{k}' is not available in {source.label}.")
    if not keys:
        raise ReportBuilderError("Select at least one column.")
    fields = [source.fields[k] for k in keys]

    flt = _normalise_filters(source, filters, strict)
    sort_field = (source.fields.get(sort_key) if sort_key else None)
    if sort_key and sort_field is None and strict:
        raise ReportBuilderError(f"Sort field '{sort_key}' is not available.")
    group_field = (source.fields.get(group_by) if group_by else None)
    if group_by and group_field is None and strict:
        raise ReportBuilderError(
            f"Group-by field '{group_by}' is not available.")
    _check_access(source, fields + [f[0] for f in flt]
                  + [x for x in (sort_field, group_field) if x], user)

    sort_dir = "desc" if str(sort_dir).lower() == "desc" else "asc"
    limit = max(1, min(int(limit or 1000), MAX_ROWS))

    plan = _build(source, fields, flt, filter_logic, sort_field, sort_dir,
                  group_field, user)
    ordered = plan.query.order_by(*plan.order)

    total = (db.session.query(func.count())
             .select_from(plan.query.order_by(None).subquery()).scalar())
    reported = min(total, limit)

    if page is not None:
        per_page = max(1, min(int(per_page or 50), 500))
        page = max(1, int(page))
        offset = (page - 1) * per_page
        take = max(0, min(per_page, limit - offset))
        records = ordered.offset(offset).limit(take).all() if take else []
    else:
        records = ordered.limit(limit).all()

    width = len(fields)
    rows = [list(r[:width]) for r in records]

    limited = ordered.limit(limit).subquery()
    totals = {}
    summed = [(i, f) for i, f in enumerate(fields) if f.aggregate == "sum"]
    if summed and reported:
        sums = db.session.query(
            *[func.sum(limited.c[f"c{i}"]) for i, _ in summed]).one()
        totals = {f.key: _round(v, f) for (_, f), v in zip(summed, sums)}

    result = {
        "columns": [_column_meta(f) for f in fields],
        "rows": rows,
        "row_count": len(rows),
        "total": total,
        "row_limit": limit,
        "truncated": total > limit,
        "totals": totals,
        "filter_logic": "OR" if str(filter_logic).upper() == "OR" else "AND",
    }
    if page is not None:
        result["page"] = page
        result["per_page"] = per_page
        result["pages"] = max(1, -(-reported // per_page))

    if group_field is not None:
        result["group"] = _column_meta(group_field)
        result["group_values"] = [r[plan.group_index] for r in records]
        aggs = [func.count()] + [func.sum(limited.c[f"c{i}"])
                                 for i, _ in summed]
        grouped = (db.session.query(limited.c.g, *aggs)
                   .group_by(limited.c.g).order_by(limited.c.g).all())
        result["groups"] = [{
            "value": g[0],
            "count": g[1],
            "totals": {f.key: _round(v, f)
                       for (_, f), v in zip(summed, g[2:])},
        } for g in grouped]
    return result


def _round(value, field):
    if value is None:
        return None
    if field.scale is None:
        return value if isinstance(value, int) else Decimal(str(value))
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-field.scale))


def _column_meta(f):
    return {"key": f.key, "label": f.label, "kind": f.kind,
            "scale": f.scale, "aggregate": f.aggregate}


# ── metadata for the builder screen ─────────────────────────────────────

def catalog(user=None):
    """Data sources this user may read, with the fields they may use,
    grouped by category, and the operators each field type allows."""
    out = []
    for key, s in DATA_SOURCES.items():
        if user is not None and not user.has_permission(s.permission):
            continue
        fields = s.visible_fields(user)
        out.append({
            "key": key,
            "label": s.label,
            "description": s.description,
            "notes": s.notes,
            "categories": [c for c in s.category_order()
                           if any(f.category == c for f in fields)],
            "fields": [{
                "key": f.key, "label": f.label, "kind": f.kind,
                "category": f.category, "aggregate": f.aggregate,
                "scale": f.scale, "help": f.help,
            } for f in fields],
        })
    return {
        "sources": out,
        "operators": OPERATOR_LABELS,
        "operators_by_kind": OPERATORS_BY_KIND,
        "max_rows": MAX_ROWS,
    }


# ── output ───────────────────────────────────────────────────────────────

def display_value(value, column):
    """A cell as plain text for JSON and CSV: exact decimals, ISO dates."""
    if value is None:
        return None
    kind = column.get("kind") if isinstance(column, dict) else None
    scale = column.get("scale") if isinstance(column, dict) else None
    if isinstance(value, bool) or kind == "boolean":
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, float)) or (kind == "number"
                                               and scale is not None):
        try:
            d = Decimal(str(value))
        except InvalidOperation:
            return str(value)
        if scale is not None:
            d = d.quantize(Decimal(1).scaleb(-scale))
        return format(d, "f")
    if isinstance(value, int):
        return value
    return str(value)


def json_result(result):
    """The result with every cell made JSON-safe and exact."""
    cols = result["columns"]
    out = dict(result)
    out["rows"] = [[display_value(v, cols[i]) for i, v in enumerate(row)]
                   for row in result["rows"]]
    by_key = {c["key"]: c for c in cols}
    out["totals"] = {k: display_value(v, by_key.get(k, {}))
                     for k, v in (result.get("totals") or {}).items()}
    if "group" in result:
        g = result["group"]
        out["group_values"] = [display_value(v, g)
                               for v in result["group_values"]]
        out["groups"] = [{
            "value": display_value(x["value"], g),
            "count": x["count"],
            "totals": {k: display_value(v, by_key.get(k, {}))
                       for k, v in x["totals"].items()},
        } for x in result["groups"]]
    return out


def to_csv(result) -> bytes:
    """UTF-8 with a BOM so Excel opens accented names correctly. Flat:
    one header row, one row per record, the group column included when
    the report is grouped."""
    import csv
    from io import StringIO
    buf = StringIO()
    writer = csv.writer(buf)
    cols = list(result["columns"])
    grouped = "group" in result and result["group"]["key"] not in [
        c["key"] for c in cols]
    header = ([result["group"]["label"]] if grouped else []) + [
        c["label"] for c in cols]
    writer.writerow(header)
    for i, row in enumerate(result["rows"]):
        cells = [display_value(v, cols[j]) for j, v in enumerate(row)]
        if grouped:
            cells = [display_value(result["group_values"][i],
                                   result["group"])] + cells
        writer.writerow(["" if c is None else c for c in cells])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def to_xlsx(result, title="Custom Report") -> bytes:
    """Render a report result as an Excel workbook, for download or for
    emailing to another system.

    Numbers are written as NUMBERS with their precision (before v315 a
    Numeric column such as an amount was written as text, so Excel could
    not sum it). Grouped reports get a heading and a subtotal per group;
    totalled columns get a grand total."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    cols = result["columns"]
    wb = Workbook()
    ws = wb.active
    ws.title = ("".join(ch for ch in title if ch not in "[]:*?/\\")[:31]
                or "Report")
    ws.append([c["label"] for c in cols])
    fill = PatternFill("solid", fgColor="1F3B4D")
    for idx in range(1, len(cols) + 1):
        cell = ws.cell(row=1, column=idx)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
    ws.freeze_panes = "A2"

    def number_format(c):
        if c.get("kind") == "date":
            return "yyyy-mm-dd"
        if c.get("kind") == "datetime":
            return "yyyy-mm-dd hh:mm"
        if c.get("kind") == "number":
            scale = c.get("scale")
            return "#,##0" + ("." + "0" * scale if scale else "")
        return None

    formats = [number_format(c) for c in cols]
    widths = [max(12, min(len(c["label"]) + 2, 40)) for c in cols]

    def put(values, bold=False):
        ws.append(values)
        r = ws.max_row
        for j, fmt in enumerate(formats):
            cell = ws.cell(row=r, column=j + 1)
            if fmt and cell.value is not None and not isinstance(
                    cell.value, str):
                cell.number_format = fmt
            if bold:
                cell.font = Font(bold=True)
            if cell.value is not None:
                widths[j] = max(widths[j], min(len(str(cell.value)) + 2, 50))

    def cell_value(v, c):
        if v is None:
            return None
        if isinstance(v, bool) or c.get("kind") == "boolean":
            return "Yes" if v else "No"
        if isinstance(v, Decimal):
            return float(v)
        if isinstance(v, (int, float, date, datetime)):
            return v
        return str(v)

    def totals_row(label, totals):
        row = [None] * len(cols)
        row[0] = label
        for j, c in enumerate(cols):
            if c["key"] in totals and totals[c["key"]] is not None:
                row[j] = cell_value(totals[c["key"]], c)
        return row

    group = result.get("group")
    groups = {display_value(g["value"], group): g
              for g in result.get("groups", [])} if group else {}
    current = None
    for i, row in enumerate(result["rows"]):
        if group:
            gv = display_value(result["group_values"][i], group)
            if i == 0 or gv != current:
                if i:
                    prev = groups.get(current)
                    if prev and prev["totals"]:
                        put(totals_row("Subtotal", prev["totals"]),
                            bold=True)
                current = gv
                info = groups.get(gv, {"count": ""})
                put([f"{group['label']}: {gv if gv is not None else '—'}"
                     f" ({info['count']})"] + [None] * (len(cols) - 1),
                    bold=True)
        put([cell_value(v, cols[j]) for j, v in enumerate(row)])
    if group and result["rows"]:
        last = groups.get(current)
        if last and last["totals"]:
            put(totals_row("Subtotal", last["totals"]), bold=True)
    if result.get("totals") and result["rows"]:
        put(totals_row("Total", result["totals"]), bold=True)

    for j, w in enumerate(widths):
        ws.column_dimensions[get_column_letter(j + 1)].width = w
    if not group and result["rows"]:
        ws.auto_filter.ref = ws.dimensions
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
