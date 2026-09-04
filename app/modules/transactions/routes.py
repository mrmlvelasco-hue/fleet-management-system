"""Transactions blueprint (Phase 3a): Trip Ticket, Authority To Drive,
Vehicle Movement. Thin controllers — all business logic lives in the
per-module services / the shared ApprovalEngine."""
from datetime import date, datetime, timedelta
import uuid

from decimal import InvalidOperation
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, abort, jsonify, current_app)
from flask_login import login_required, current_user

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.core.approval.engine import (
    NotEligibleApproverError, InvalidStateError)
from app.core.validation.date_utils import (
    parse_form_date, parse_form_datetime, DateFormatError, RequiredFieldError)
from app.modules.transactions.base_service import NotVisibleError
from app.extensions import db

from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.driver.service import DriverService

from app.modules.transactions.trip_ticket.service import (
    TripTicketService, DriverRequiredError, InvalidTripStateError)
from app.modules.transactions.atd.service import (
    ATDService, InvalidATDStateError)
from app.modules.transactions.atd.models import AuthorityToDrive
from app.modules.transactions.vehicle_movement.service import (
    VehicleMovementService, InvalidMovementTypeError)
from app.modules.transactions.vehicle_movement.models import VehicleMovement
from app.modules.transactions.trip_ticket.models import TripTicket
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService, IncompleteChecklistError, InvalidOrderStateError,
    InvalidOrderCategoryError)
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.transactions.maintenance_invoice.service import (
    MaintenanceInvoiceService, InvoiceLockedError)
from app.modules.transactions.maintenance_invoice.models import (
    MaintenanceInvoice, MaintenanceInvoiceLine)
from app.modules.transactions.tire_txn.service import (
    TireTransactionService, InvalidTireActionError)
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService, InvalidBatteryActionError)
from app.modules.transactions.battery_txn.models import BatteryTransaction
from app.modules.transactions.purchase_request.service import (
    PurchaseRequestService, LineManagementError)
from app.modules.transactions.purchase_request.models import PurchaseRequest
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService, DuplicateActiveRegistrationError,
    NoExistingRegistrationError, InvalidRegistrationStateError)
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)

bp = Blueprint("transactions", __name__, url_prefix="/transactions",
               template_folder="templates")

for _mod in ["tripticket", "atd", "vehiclemovement", "maintenanceorder",
             "tiretxn", "batterytxn", "purchaserequest", "vehicleregistration",
             "maintenanceinvoice", "checklist"]:
    for _act in ["view", "create", "update", "delete", "print"]:
        _code = f"{_mod}.{_act}"
        registry.register(_code, _mod, _act, f"{_act.title()} {_mod}")
for _code, _act in (("checklist.submit", "submit"),
                    # Split out of checklist.submit, which was doing two
                    # unrelated jobs: "may finalise" AND "is a
                    # reviewer". Drivers now hold submit so they can
                    # finalise their OWN inspection; review stays with
                    # Fleet and is what widens visibility to everyone's.
                    ("checklist.review", "review"),
                    ("checklist.report", "report"),
                    ("checklist.manage", "manage")):
    registry.register(_code, "checklist", _act, f"{_act.title()} checklist")


def _flash_engine_error(exc):
    """Show the exception's message directly ONLY when it is one of
    this codebase's own deliberately-raised business-rule exceptions
    (InvalidOrderStateError, NoExistingRegistrationError, and so on) --
    those are hand-written to be read by the person using the system.

    Anything else -- a raw SQLAlchemy/database error, a third-party
    library exception -- was never written for a human audience and
    must not be shown verbatim: it can include the full SQL statement,
    real column names, and literal parameter values, exactly as
    happened in a real reported incident (a duplicate-key error shown
    to the end user included the entire INSERT statement). That gets a
    generic, safe message with a reference code instead; the real
    detail still goes to the server log, same pattern as the global 500
    handler.
    """
    if type(exc).__module__.startswith("app."):
        flash(str(exc), "danger")
        return
    ref = uuid.uuid4().hex[:8].upper()
    current_app.logger.exception("Flashed error [ref=%s]: %s", ref, exc)
    flash(f"Something went wrong completing this action "
         f"(reference {ref}). Please try again, and let your "
         f"administrator know if it keeps happening.", "danger")


def _print_report_context(item):
    """Shared data every 'Dynamic Report' print template needs: company
    letterhead, the real approval chain (for actual signature names/dates
    instead of blank lines), attachments, and a print timestamp. Reused
    across every transaction module's print route rather than duplicating
    this fetch logic in each one."""
    from datetime import datetime
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    from app.modules.master_data.routes import _attachment_rows
    from app.core.approval.engine import ApprovalEngine
    return {
        "company": CompanyProfileService().get(),
        "approval_chain_data": (ApprovalEngine().get_approval_chain(item.approval_instance)
                               if item.approval_instance else []),
        "attachments": (_attachment_rows(item.__tablename__, item.id)
                        if item else []),
        "print_timestamp": datetime.now().strftime("%m/%d/%Y - %H:%M"),
    }


# ── Trip Tickets ────────────────────────────────────────────────────────────

@bp.route("/trip-tickets")
@login_required
@require_permission("tripticket.view")
def tripticket_list():
    return render_template(
        "transactions/tripticket_list.html",
        **_txn_list_context(TripTicketService()))


@bp.route("/trip-tickets/new", methods=["GET", "POST"])
@login_required
@require_permission("tripticket.create")
def tripticket_new():
    if request.method == "POST":
        f = request.form
        try:
            TripTicketService().create(
                vehicle_id=int(f["vehicle_id"]),
                driver_id=int(f["driver_id"]) if f.get("driver_id") else None,
                driver_name_manual=f.get("driver_name_manual") or None,
                destination=f["destination"], purpose=f["purpose"],
                departure_datetime=parse_form_datetime(
                    f.get("departure_datetime"), "Departure Date/Time",
                    required=True),
                odometer_out=int(f["odometer_out"]) if f.get("odometer_out") else None,
                passengers=f.get("passengers"), user=current_user)
            flash("Trip Ticket created.", "success")
            return redirect(url_for("transactions.tripticket_list"))
        except (DriverRequiredError, DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/tripticket_form.html",
                           title="New Trip Ticket")


@bp.route("/trip-tickets/<int:tid>")
@login_required
@require_permission("tripticket.view")
def tripticket_detail(tid):
    item = TripTicketService().get_visible(tid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/tripticket_detail.html", item=item)


@bp.route("/trip-tickets/<int:tid>/print")
@login_required
@require_permission("tripticket.print")
def tripticket_print(tid):
    item = db.session.get(TripTicket, tid)
    return render_template("transactions/tripticket_print.html", item=item,
                           **_print_report_context(item))


@bp.route("/trip-tickets/<int:tid>/submit", methods=["POST"])
@login_required
@require_permission("tripticket.update")
def tripticket_submit(tid):
    try:
        TripTicketService().submit(tid, user=current_user)
        flash("Trip Ticket submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/approve", methods=["POST"])
@login_required
@require_permission("tripticket.view")
def tripticket_approve(tid):
    try:
        TripTicketService().approve(tid, user=current_user,
                                    remarks=request.form.get("remarks"))
        flash("Trip Ticket approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/reject", methods=["POST"])
@login_required
@require_permission("tripticket.view")
def tripticket_reject(tid):
    try:
        TripTicketService().reject(tid, user=current_user,
                                   remarks=request.form.get("remarks"))
        flash("Trip Ticket rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/return", methods=["POST"])
@login_required
@require_permission("tripticket.view")
def tripticket_return(tid):
    try:
        TripTicketService().return_document(
            tid, user=current_user, remarks=request.form.get("remarks"))
        flash("Trip Ticket returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/cancel", methods=["POST"])
@login_required
@require_permission("tripticket.update")
def tripticket_cancel(tid):
    try:
        TripTicketService().cancel(tid, user=current_user)
        flash("Trip Ticket cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/release", methods=["POST"])
@login_required
@require_permission("tripticket.update")
def tripticket_release(tid):
    try:
        TripTicketService().release(tid)
        flash("Vehicle released for trip.", "success")
    except InvalidTripStateError as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


@bp.route("/trip-tickets/<int:tid>/complete", methods=["POST"])
@login_required
@require_permission("tripticket.update")
def tripticket_complete(tid):
    f = request.form
    try:
        TripTicketService().complete(
            tid, odometer_in=int(f["odometer_in"]),
            return_datetime=parse_form_datetime(
                f.get("return_datetime"), "Return Date/Time", required=True))
        flash("Trip Ticket marked complete.", "success")
    except (DateFormatError, RequiredFieldError) as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


# ── Authority To Drive ──────────────────────────────────────────────────────

@bp.route("/atd")
@login_required
@require_permission("atd.view")
def atd_list():
    return render_template(
        "transactions/atd_list.html",
        **_txn_list_context(ATDService()))


@bp.route("/atd/new", methods=["GET", "POST"])
@login_required
@require_permission("atd.create")
def atd_new():
    from app.modules.master_data.vehicle.service import VehicleService as _VS
    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    prefill_vehicle = None
    prefill_order = None
    if request.method == "GET":
        if request.args.get("vehicle_id"):
            prefill_vehicle = _VS().get(int(request.args["vehicle_id"]))
        if request.args.get("maintenance_order_id"):
            prefill_order = db.session.get(
                MaintenanceOrder, int(request.args["maintenance_order_id"]))
    prefill = {
        "purpose": request.args.get("purpose"),
        "valid_from": request.args.get("valid_from"),
        "valid_to": request.args.get("valid_to"),
    }
    if request.method == "POST":
        f = request.form
        try:
            ATDService().create(
                vehicle_id=int(f["vehicle_id"]), driver_id=int(f["driver_id"]),
                purpose=f["purpose"],
                valid_from=parse_form_date(f.get("valid_from"), "Valid From",
                                           required=True),
                valid_to=parse_form_date(f.get("valid_to"), "Valid To",
                                         required=True),
                maintenance_order_id=int(f["maintenance_order_id"]) if f.get("maintenance_order_id") else None,
                odometer_out=int(f["odometer_out"]) if f.get("odometer_out") else None,
                user=current_user)
            flash("Authority To Drive created.", "success")
            return redirect(url_for("transactions.atd_list"))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/atd_form.html",
                           prefill_vehicle=prefill_vehicle,
                           prefill_order=prefill_order, prefill=prefill,
                           title="New Authority To Drive")


@bp.route("/atd/<int:aid>/record-odometer-in", methods=["POST"])
@login_required
@require_permission("atd.update")
def atd_record_odometer_in(aid):
    odometer_in = request.form.get("odometer_in")
    if odometer_in:
        ATDService().record_odometer_in(aid, odometer_in=int(odometer_in))
        flash("Odometer (In) recorded.", "success")
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>")
@login_required
@require_permission("atd.view")
def atd_detail(aid):
    item = ATDService().get_visible(aid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/atd_detail.html", item=item)


@bp.route("/atd/<int:aid>/print")
@login_required
@require_permission("atd.print")
def atd_print(aid):
    from datetime import datetime
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    from app.core.approval.engine import ApprovalEngine
    item = db.session.get(AuthorityToDrive, aid)
    company = CompanyProfileService().get()
    approval_chain_data = (ApprovalEngine().get_approval_chain(item.approval_instance)
                           if item.approval_instance else [])
    return render_template("transactions/atd_print.html", item=item,
                           company=company,
                           approval_chain_data=approval_chain_data,
                           print_timestamp=datetime.now().strftime("%m/%d/%Y - %H:%M"))


@bp.route("/atd/<int:aid>/submit", methods=["POST"])
@login_required
@require_permission("atd.update")
def atd_submit(aid):
    try:
        ATDService().submit(aid, user=current_user)
        flash("ATD submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>/approve", methods=["POST"])
@login_required
@require_permission("atd.view")
def atd_approve(aid):
    try:
        ATDService().approve(aid, user=current_user,
                             remarks=request.form.get("remarks"))
        flash("ATD approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>/reject", methods=["POST"])
@login_required
@require_permission("atd.view")
def atd_reject(aid):
    try:
        ATDService().reject(aid, user=current_user,
                            remarks=request.form.get("remarks"))
        flash("ATD rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>/return", methods=["POST"])
@login_required
@require_permission("atd.view")
def atd_return(aid):
    try:
        ATDService().return_document(aid, user=current_user,
                                     remarks=request.form.get("remarks"))
        flash("ATD returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>/cancel", methods=["POST"])
@login_required
@require_permission("atd.update")
def atd_cancel(aid):
    try:
        ATDService().cancel(aid, user=current_user)
        flash("ATD cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


@bp.route("/atd/<int:aid>/activate", methods=["POST"])
@login_required
@require_permission("atd.update")
def atd_activate(aid):
    try:
        ATDService().activate(aid)
        flash("ATD activated.", "success")
    except InvalidATDStateError as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.atd_detail", aid=aid))


# ── Vehicle Movement ─────────────────────────────────────────────────────────

@bp.route("/vehicle-movements")
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_list():
    return render_template(
        "transactions/vehiclemovement_list.html",
        **_txn_list_context(VehicleMovementService()))


@bp.route("/vehicle-movements/new", methods=["GET", "POST"])
@login_required
@require_permission("vehiclemovement.create")
def vehiclemovement_new():
    from app.modules.system_admin.services.lookup_service import LookupService
    movement_types = LookupService().get_by_type_with_fallback("MOVEMENT_TYPE")
    if request.method == "POST":
        f = request.form
        try:
            VehicleMovementService().create(
                vehicle_id=int(f["vehicle_id"]),
                movement_type=f["movement_type"],
                from_location=f["from_location"],
                to_location=f["to_location"],
                movement_date=parse_form_date(f.get("movement_date"),
                                              "Movement Date", required=True),
                driver_id=int(f["driver_id"]) if f.get("driver_id") else None,
                employee_responsible=f.get("employee_responsible") or None,
                purpose=f.get("purpose") or None,
                movement_start_datetime=parse_form_datetime(
                    f.get("movement_start_datetime"), "Movement Start Date/Time"),
                remarks=f.get("remarks"), user=current_user)
            flash("Vehicle Movement created.", "success")
            return redirect(url_for("transactions.vehiclemovement_list"))
        except (InvalidMovementTypeError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/vehiclemovement_form.html",
                           movement_types=movement_types,
                           title="New Vehicle Movement")


@bp.route("/vehicle-movements/<int:mid>")
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_detail(mid):
    item = VehicleMovementService().get_visible(mid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/vehiclemovement_detail.html", item=item)


@bp.route("/vehicle-movements/<int:mid>/print")
@login_required
@require_permission("vehiclemovement.print")
def vehiclemovement_print(mid):
    item = db.session.get(VehicleMovement, mid)
    return render_template("transactions/vehiclemovement_print.html",
                           item=item, **_print_report_context(item))


@bp.route("/vehicle-movements/<int:mid>/submit", methods=["POST"])
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_submit(mid):
    try:
        VehicleMovementService().submit(mid, user=current_user)
        flash("Vehicle Movement submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/approve", methods=["POST"])
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_approve(mid):
    try:
        VehicleMovementService().approve(mid, user=current_user,
                                         remarks=request.form.get("remarks"))
        flash("Vehicle Movement approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/reject", methods=["POST"])
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_reject(mid):
    try:
        VehicleMovementService().reject(mid, user=current_user,
                                        remarks=request.form.get("remarks"))
        flash("Vehicle Movement rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/return", methods=["POST"])
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_return(mid):
    try:
        VehicleMovementService().return_document(
            mid, user=current_user, remarks=request.form.get("remarks"))
        flash("Vehicle Movement returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/cancel", methods=["POST"])
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_cancel(mid):
    try:
        VehicleMovementService().cancel(mid, user=current_user)
        flash("Vehicle Movement cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/start-transit", methods=["POST"])
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_start_transit(mid):
    VehicleMovementService().start_transit(mid)
    flash("Movement in transit.", "success")
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/complete", methods=["POST"])
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_complete(mid):
    f = request.form
    try:
        VehicleMovementService().complete(
            mid, movement_end_datetime=parse_form_datetime(
                f.get("movement_end_datetime"), "Movement End Date/Time"))
        flash("Vehicle Movement completed.", "success")
    except (DateFormatError, RequiredFieldError) as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


# ── Maintenance Orders ───────────────────────────────────────────────────────

@bp.route("/maintenance-orders")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_list():
    # Classification is specific to Maintenance Orders, so it's passed
    # as an extra filter rather than added to the shared bar every other
    # list would then have to ignore.
    svc = MaintenanceOrderService()
    cls = request.args.get("maintenance_class")
    # Classification is derived, not a stored column -- the service owns
    # the SQL for it so it stays in step with the property that renders
    # the column.
    extra = [svc.maintenance_class_clause(cls)] if cls else []
    ctx = _txn_list_context(svc, extra_filters=extra)
    ctx["maintenance_class"] = cls or ""
    # Module-specific filters must count toward "is anything filtered",
    # or the empty state wrongly reads "no orders yet" when the real
    # answer is "none match what you selected".
    if cls:
        ctx["filters"]["has_any"] = True
    return render_template("transactions/maintenanceorder_list.html", **ctx)



# Numeric form fields, mapped to the label the person actually sees, so a
# coercion failure can name the field instead of surfacing Python's own
# "invalid literal for int()" text.
_NUMERIC_FIELD_LABELS = {
    "odometer_at_service": "Odometer at Service",
    "estimated_cost": "Estimated Cost",
    "actual_cost": "Actual Cost",
    "disposal_value": "Disposal Value",
    "current_odometer": "Current Odometer",
    "year": "Year",
}


def _friendly_number_error(exc, form):
    """Turn a raw int()/Decimal() failure into a message naming the field.

    Finds the offending value inside the exception text and matches it
    back to whichever numeric field holds it -- the exception itself
    carries the value but not the field name.
    """
    text = str(exc)
    for name, label in _NUMERIC_FIELD_LABELS.items():
        value = (form.get(name) or "").strip()
        if value and value in text:
            whole = name.endswith(("odometer", "odometer_at_service", "year"))
            return (f"{label}: '{value}' is not a valid "
                   f"{'whole number' if whole else 'number'}."
                   + (" Enter a whole number without decimals."
                      if whole else ""))
    return ("One of the numeric fields contains a value that isn't a "
           "number. Please check the odometer, cost and year fields.")


@bp.route("/maintenance-orders/new", methods=["GET", "POST"])
@login_required
@require_permission("maintenanceorder.create")
def maintenanceorder_new():
    # Holds what the person typed when validation fails, so the form can
    # be re-rendered with their input intact. Previously a single bad
    # field cleared the whole form and they had to retype everything --
    # which is what made the category/transaction-type mismatch so
    # painful to correct.
    submitted = None
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.maintenance_config.service import (
        PMScheduleService, PMScopeTemplateService)
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    from itertools import groupby

    maintenance_types = MaintenanceTypeService().list()
    all_transaction_types = TransactionTypeService().list()
    transaction_types_by_group = {
        group: list(items) for group, items in
        groupby(all_transaction_types, key=lambda t: t.group or "Other")}

    # Optional pre-fill via query params — used by the Dashboard's
    # "Vehicles Due for Maintenance" widget so clicking a due item goes
    # straight into a ready-to-submit order instead of just the vehicle's
    # detail page.
    prefill_vehicle = None
    if request.method == "GET" and request.args.get("vehicle_id"):
        prefill_vehicle = VehicleService().get(int(request.args["vehicle_id"]))
    prefill = {
        "vehicle_id": request.args.get("vehicle_id", type=int),
        "order_category": request.args.get("order_category", "MAINTENANCE"),
        "transaction_type_id": request.args.get("transaction_type_id", type=int),
        "maintenance_type_id": request.args.get("maintenance_type_id", type=int),
        "scope_template_id": request.args.get("scope_template_id", type=int),
        "odometer_at_service": request.args.get("odometer_at_service", type=int),
        "scheduled_date": request.args.get("scheduled_date"),
    }

    # Scope Template list must only ever show what's actually applicable
    # to the selected vehicle (Brand/Model, then Vehicle Type, then
    # global) — never the entire system-wide list, which was the
    # reported bug (an unrelated vehicle's checklist showing up as a
    # selectable option). With no vehicle chosen yet, there's nothing
    # to filter by, so the list starts empty and the page's own JS
    # re-fetches it the moment a vehicle is picked.
    scope_templates = []
    due_scope_template_id = None
    pm_recommendation = None
    from app.modules.system_admin.services.lookup_service import LookupService
    assignment_classifications = LookupService().get_by_type_with_fallback(
        "ASSIGNMENT_CLASSIFICATION")
    if prefill_vehicle:
        scope_templates = PMScopeTemplateService().list_applicable_for_vehicle(
            prefill_vehicle, maintenance_type_id=prefill["maintenance_type_id"])
        pm_recommendation = PMScopeTemplateService().get_next_due_recommendation(
            prefill_vehicle, maintenance_type_id=prefill["maintenance_type_id"])
        due_template = PMScopeTemplateService().get_next_due_scope_template(
            prefill_vehicle, maintenance_type_id=prefill["maintenance_type_id"])
        due_scope_template_id = due_template.id if due_template else None
        if not prefill.get("scope_template_id") and due_scope_template_id:
            prefill["scope_template_id"] = due_scope_template_id

    if request.method == "POST":
        f = request.form
        order_category = f.get("order_category", "MAINTENANCE")
        try:
            MaintenanceOrderService().create(
                vehicle_id=int(f["vehicle_id"]),
                order_category=order_category,
                transaction_type_id=int(f["transaction_type_id"]) if f.get("transaction_type_id") else None,
                maintenance_type_id=int(f["maintenance_type_id"]) if f.get("maintenance_type_id") else None,
                scope_template_id=int(f["scope_template_id"]) if f.get("scope_template_id") else None,
                scheduled_date=parse_form_date(f.get("scheduled_date"),
                                               "Scheduled Date", required=True),
                odometer_at_service=int(f["odometer_at_service"]) if f.get("odometer_at_service") else None,
                description=f.get("description"),
                assigned_mechanic=f.get("assigned_mechanic"),
                vendor_id=int(f["vendor_id"]) if f.get("vendor_id") else None,
                estimated_cost=f.get("estimated_cost") or None,
                driver_id=int(f["driver_id"]) if f.get("driver_id") else None,
                destination_branch_id=int(f["destination_branch_id"])
                                     if f.get("destination_branch_id") else None,
                disposal_value=f.get("disposal_value") or None,
                disposal_recipient=f.get("disposal_recipient") or None,
                assignment_classification=f.get("assignment_classification") or None,
                user=current_user)
            flash("Maintenance Order created.", "success")
            return redirect(url_for("transactions.maintenanceorder_list"))
        except (DateFormatError, RequiredFieldError,
                InvalidOrderCategoryError) as e:
            flash(str(e), "danger")
            submitted = f
        except ValueError as e:
            # A raw "invalid literal for int() with base 10: '15.4'"
            # tells the person nothing about WHICH field to fix. Map it
            # back to a named field wherever we can.
            flash(_friendly_number_error(e, f), "danger")
            submitted = f
        except Exception as e:
            _flash_engine_error(e)
            submitted = f
    from app.modules.master_data.org.models import Branch
    return render_template("transactions/maintenanceorder_form.html",
                           submitted=submitted,
                           maintenance_types=maintenance_types,
                           transaction_types_by_group=transaction_types_by_group,
                           scope_templates=scope_templates,
                           prefill_vehicle=prefill_vehicle, prefill=prefill,
                           due_scope_template_id=due_scope_template_id,
                           pm_recommendation=pm_recommendation,
                           assignment_classifications=assignment_classifications,
                           branches=Branch.query.filter_by(is_active=True)
                                   .order_by(Branch.name).all(),
                           title="New Maintenance Order")


@bp.route("/maintenance-orders/<int:oid>")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_detail(oid):
    svc = MaintenanceOrderService()
    item = svc.get_visible(oid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/maintenanceorder_detail.html",
                           item=item,
                           editable_scope=svc.editable_scope(item))


@bp.route("/maintenance-orders/<int:oid>/edit", methods=["GET", "POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_edit(oid):
    """Edit an order that is still DRAFT (or RETURNED), or add remarks to
    one already in progress.

    Deliberately scoped by BOTH who is asking and what state the order is
    in -- see MaintenanceOrderService.editable_scope() for why "DRAFT"
    alone isn't a sufficient test.
    """
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService)
    from app.modules.system_admin.services.lookup_service import LookupService

    svc = MaintenanceOrderService()
    item = db.session.get(MaintenanceOrder, oid)
    if item is None:
        flash("Maintenance Order not found.", "warning")
        return redirect(url_for("transactions.maintenanceorder_list"))

    # The initiator is who this is for. Someone with broader rights (a
    # fleet admin holding maintenanceorder.approve) can also correct an
    # order, but an ordinary user must not be able to edit somebody
    # else's request.
    is_initiator = item.requested_by == current_user.id
    if not is_initiator and not current_user.has_permission(
            "maintenanceorder.approve"):
        flash("You can only edit Maintenance Orders that you raised.",
              "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))

    scope = svc.editable_scope(item)
    if scope == "NONE":
        if item.status == "DRAFT" and item.approval_instance is not None:
            flash("This order is awaiting approval and cannot be edited. "
                  "Ask the approver to return it if it needs changes.",
                  "warning")
        else:
            flash(f"A {item.status} order can no longer be edited.", "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))

    if request.method == "POST":
        f = request.form
        try:
            if scope == "REMARKS_ONLY":
                svc.update(oid, user=current_user,
                          description=f.get("description") or None)
            else:
                def _int(name):
                    raw = f.get(name)
                    return int(raw) if raw else None

                svc.update(
                    oid, user=current_user,
                    description=f.get("description") or None,
                    scheduled_date=parse_form_date(
                        f.get("scheduled_date"), "Scheduled Date",
                        required=True),
                    odometer_at_service=_int("odometer_at_service"),
                    estimated_cost=f.get("estimated_cost") or None,
                    assigned_mechanic=f.get("assigned_mechanic") or None,
                    vendor_id=_int("vendor_id"),
                    maintenance_type_id=_int("maintenance_type_id"),
                    transaction_type_id=_int("transaction_type_id"),
                    scope_template_id=_int("scope_template_id"),
                    driver_id=_int("driver_id"),
                    destination_branch_id=_int("destination_branch_id"),
                    assignment_classification=(
                        f.get("assignment_classification") or None),
                )
            flash("Maintenance Order updated.", "success")
            return redirect(url_for("transactions.maintenanceorder_detail",
                                   oid=oid))
        except Exception as exc:
            _flash_engine_error(exc)

    return render_template("transactions/maintenanceorder_edit.html",
                           item=item, scope=scope,
                           maintenance_types=MaintenanceTypeService().list(),
                           transaction_types=TransactionTypeService().list(),
                           assignment_classifications=LookupService()
                           .get_by_type_with_fallback(
                               "ASSIGNMENT_CLASSIFICATION"))


@bp.route("/maintenance-orders/<int:oid>/print-disposal")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_print_disposal(oid):
    """Asset Disposal Report (ADR) — the retirement-stage document,
    completing the Acquisition-to-Retirement asset lifecycle. Only
    meaningful for a completed Disposal-group order."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(MaintenanceOrder, oid)
    if item is None or not item.disposal_reference_number:
        flash("This order has no disposal reference number — the Asset "
             "Disposal Report only applies to completed Disposal-group "
             "orders.", "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))
    company = CompanyProfileService().get()
    return render_template("transactions/maintenanceorder_print_disposal.html",
                           item=item, company=company,
                           generated_at=datetime.now())


@bp.route("/maintenance-orders/<int:oid>/print-vam")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_print_vam(oid):
    """Vehicle Assignment Memo (VAM) — the formal company memo for
    Assignment/Reassignment orders, matching the corporate paper form.
    Endorsed By / Approved By are pulled from the REAL approval trail
    (ApprovalTask, ordered by level) rather than typed in, since "This
    VAM was electronically approved hence not requiring any signature"
    is only true if it reflects what was actually approved."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    from app.modules.user_management.models import User
    item = db.session.get(MaintenanceOrder, oid)
    if item is None or not item.driver_id:
        flash("This order has no Driver/Assignee set — the Vehicle "
             "Assignment Memo only applies to Assignment/Reassignment "
             "orders.", "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))

    company = CompanyProfileService().get()

    # Endorsed By = the person who INITIATED the memo (the MO's requester)
    # with the CREATED date. Approved By = the actual final approver from
    # the approval trail with the APPROVAL date. These are two different
    # people playing two different roles on the form -- the earlier
    # version wrongly pulled BOTH rows from the approval trail, which
    # left "Approved by" blank whenever there was only one approval level
    # and put the approver (not the initiator) on the "Endorsed by" line.
    endorsed_by = None
    if item.requester:
        role_name = (item.requester.roles[0].name
                    if item.requester.roles else "")
        endorsed_by = {
            "name": item.requester.full_name, "title": role_name,
            "date": item.created_at.date() if item.created_at else None,
        }

    approved_by = None
    if item.approval_instance:
        from app.core.approval.models import ApprovalTask
        # The FINAL approval level that actually completed the memo --
        # the highest-level COMPLETED task. (ApprovalTask has no `action`
        # column; a COMPLETED task represents a passed approval level,
        # since a rejection cancels the whole instance rather than
        # completing a task.)
        final_task = (ApprovalTask.query
                     .filter_by(approval_instance_id=item.approval_instance.id,
                               status="COMPLETED")
                     .order_by(ApprovalTask.level_number.desc()).first())
        if final_task and final_task.completed_by:
            approver = db.session.get(User, final_task.completed_by)
            if approver:
                role_name = approver.roles[0].name if approver.roles else ""
                approved_by = {
                    "name": approver.full_name, "title": role_name,
                    "date": (final_task.completed_at.date()
                            if final_task.completed_at else None),
                }

    latest_reg = None
    try:
        from app.modules.transactions.vehicle_registration.models import (
            VehicleRegistration)
        latest_reg = (VehicleRegistration.query
                     .filter_by(vehicle_id=item.vehicle_id, status="COMPLETED")
                     .filter(VehicleRegistration.expiry_date.isnot(None))
                     .order_by(VehicleRegistration.expiry_date.desc())
                     .first())
    except Exception:
        pass

    # The Oath of Undertaking is issued WITH the memo -- the assignee
    # signs both. `?oath=0` prints the memo alone, for a reprint filed
    # after the undertaking has already been signed once.
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    tpl_svc = PrintTemplateService()
    include_oath = request.args.get("oath", "1") != "0"
    oath_tpl = tpl_svc.get("OATH_OF_UNDERTAKING") if include_oath else None
    oath_body = (tpl_svc.render(oath_tpl.body_html, order=item)
                if oath_tpl else None)

    # Front/back photos for the Conforme page, by document type rather
    # than by filename, so a rename cannot silently blank the page.
    from app.core.models.attachment import Attachment
    def _photo(*tokens):
        if not item.vehicle_id:
            return None
        rows = (Attachment.query
                .filter_by(reference_table="vehicles",
                           reference_id=item.vehicle_id)
                .order_by(Attachment.created_at.desc()).all())
        want = [x.lower().replace(" ", "_") for x in tokens]
        for a in rows:
            if not getattr(a, "is_active", True):
                continue
            blob = f"{(a.document_type or '')} {(a.original_filename or '')}".lower()
            if any(tok in blob.replace(" ", "_") or tok in blob for tok in want):
                if "front" in want and "back" in blob and "front" not in blob:
                    continue
                if "back" in want and "front" in blob and "back" not in blob:
                    continue
                return a
        images = [a for a in rows if getattr(a, "is_active", True)
                  and (a.mime_type or "").startswith("image/")]
        return images[0] if images else None

    return render_template("transactions/maintenanceorder_print_vam.html",
                           item=item, company=company,
                           endorsed_by=endorsed_by, approved_by=approved_by,
                           latest_reg=latest_reg,
                           oath_template=oath_tpl, oath_body=oath_body,
                           photo_front=_photo("PHOTO_FRONT", "front"),
                           photo_back=_photo("PHOTO_BACK", "back"),
                           generated_at=datetime.now())


@bp.route("/maintenance-orders/<int:oid>/print-transfer")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_print_transfer(oid):
    """Asset Transfer Report (ATR) — the branch-to-branch vehicle
    ownership/custody transfer document, only meaningful for a
    Relocation/Transfer order that has a destination branch set."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(MaintenanceOrder, oid)
    if item is None or not item.destination_branch_id:
        flash("This order has no destination branch set — the Asset "
             "Transfer Report only applies to Relocation/Transfer orders.",
             "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))
    company = CompanyProfileService().get()
    return render_template("transactions/maintenanceorder_print_transfer.html",
                           item=item, company=company,
                           generated_at=datetime.now())


@bp.route("/maintenance-orders/<int:oid>/print")
@login_required
@require_permission("maintenanceorder.print")
def maintenanceorder_print(oid):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    from app.modules.master_data.routes import _attachment_rows
    from app.core.approval.engine import ApprovalEngine
    item = db.session.get(MaintenanceOrder, oid)
    company = CompanyProfileService().get()
    # PM8 — Last Completed Work Order: the most recent COMPLETED order for
    # this same vehicle before this one, for the "PM Parameter Mapping"
    # block the Dynamic PM Work Order Report spec calls for.
    last_completed = (MaintenanceOrder.query
                      .filter(MaintenanceOrder.vehicle_id == item.vehicle_id,
                             MaintenanceOrder.id != item.id,
                             MaintenanceOrder.status == "COMPLETED")
                      .order_by(MaintenanceOrder.completed_date.desc())
                      .first())
    attachments = _attachment_rows("maintenance_orders", oid)
    approval_chain_data = (ApprovalEngine().get_approval_chain(item.approval_instance)
                           if item.approval_instance else [])
    return render_template("transactions/maintenanceorder_print.html",
                           item=item, company=company,
                           last_completed=last_completed,
                           attachments=attachments,
                           approval_chain_data=approval_chain_data)


@bp.route("/maintenance-orders/<int:oid>/submit", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_submit(oid):
    try:
        MaintenanceOrderService().submit(oid, user=current_user)
        flash("Maintenance Order submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/resubmit", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_resubmit(oid):
    """Resubmit a RETURNED order back into the approval flow. Without
    this the lifecycle dead-ended: once an approver RETURNED an order,
    the detail page only offered Cancel, so the requester could never
    act on the feedback and send it back -- they had to abandon the
    order and re-key a new one."""
    try:
        MaintenanceOrderService().resubmit(oid, user=current_user)
        flash("Maintenance Order resubmitted for approval.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/approve", methods=["POST"])
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_approve(oid):
    try:
        MaintenanceOrderService().approve(oid, user=current_user,
                                          remarks=request.form.get("remarks"))
        flash("Maintenance Order approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/reject", methods=["POST"])
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_reject(oid):
    try:
        MaintenanceOrderService().reject(oid, user=current_user,
                                         remarks=request.form.get("remarks"))
        flash("Maintenance Order rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/return", methods=["POST"])
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_return(oid):
    try:
        MaintenanceOrderService().return_document(
            oid, user=current_user, remarks=request.form.get("remarks"))
        flash("Maintenance Order returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/cancel", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_cancel(oid):
    try:
        MaintenanceOrderService().cancel(oid, user=current_user)
        flash("Maintenance Order cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/start-work", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_start_work(oid):
    MaintenanceOrderService().start_work(oid)
    flash("Maintenance work started.", "success")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/checklist/<int:item_id>/toggle",
          methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_checklist_toggle(oid, item_id):
    done = request.form.get("done") == "1"
    try:
        MaintenanceOrderService().toggle_checklist_item(
            item_id, done=done, user=current_user)
    except InvalidOrderStateError as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/checklist/<int:item_id>/toggle-ajax",
          methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_checklist_toggle_ajax(oid, item_id):
    """JSON variant of the toggle above -- lets the detail page update a
    single checklist row in place (no navigation, no lost scroll position)
    instead of a full form POST-redirect-GET back to the top of the page."""
    done = request.form.get("done") == "1"
    try:
        item = MaintenanceOrderService().toggle_checklist_item(
            item_id, done=done, user=current_user)
        return jsonify(ok=True, item_id=item.id, is_done=item.is_done)
    except InvalidOrderStateError as e:
        return jsonify(ok=False, error=str(e)), 409


@bp.route("/maintenance-orders/<int:oid>/checklist/mark-all-ajax",
          methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_checklist_mark_all_ajax(oid):
    """Mark every checklist item on the order done (or undone) in a single
    request -- backs the "Mark All Done" button for scopes with many
    checklist lines."""
    done = request.form.get("done", "1") == "1"
    try:
        items = MaintenanceOrderService().mark_all_checklist_items(
            oid, done=done, user=current_user)
        return jsonify(ok=True, items=[
            {"item_id": i.id, "is_done": i.is_done} for i in items])
    except InvalidOrderStateError as e:
        return jsonify(ok=False, error=str(e)), 409


@bp.route("/maintenance-orders/<int:oid>/complete", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_complete(oid):
    f = request.form
    try:
        MaintenanceOrderService().complete(
            oid, actual_cost=f.get("actual_cost") or None,
            completed_date=parse_form_date(f.get("completed_date"),
                                           "Completed Date", required=True))
        flash("Maintenance Order marked complete.", "success")
    except (IncompleteChecklistError, DateFormatError, RequiredFieldError) as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


# ── Tire Transactions ────────────────────────────────────────────────────────

@bp.route("/tire-transactions")
@login_required
@require_permission("tiretxn.view")
def tiretxn_list():
    return render_template(
        "transactions/tiretxn_list.html",
        **_txn_list_context(TireTransactionService()))


@bp.route("/tire-transactions/new", methods=["GET", "POST"])
@login_required
@require_permission("tiretxn.create")
def tiretxn_new():
    from app.modules.master_data.tire.service import TireService
    tires = TireService().list()
    if request.method == "POST":
        f = request.form
        try:
            TireTransactionService().create(
                tire_id=int(f["tire_id"]),
                vehicle_id=int(f["vehicle_id"]) if f.get("vehicle_id") else None,
                action=f["action"],
                transaction_date=parse_form_date(f.get("transaction_date"),
                                                 "Transaction Date", required=True),
                odometer_at_service=int(f["odometer_at_service"]) if f.get("odometer_at_service") else None,
                remarks=f.get("remarks"), user=current_user)
            flash("Tire Transaction recorded.", "success")
            return redirect(url_for("transactions.tiretxn_list"))
        except (InvalidTireActionError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/tiretxn_form.html", tires=tires,
                           title="New Tire Transaction")


@bp.route("/tire-transactions/<int:tid>")
@login_required
@require_permission("tiretxn.view")
def tiretxn_detail(tid):
    item = TireTransactionService().get_visible(tid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/tiretxn_detail.html", item=item)


@bp.route("/tire-transactions/<int:tid>/submit", methods=["POST"])
@login_required
@require_permission("tiretxn.update")
def tiretxn_submit(tid):
    try:
        TireTransactionService().submit(tid, user=current_user)
        flash("Tire Transaction submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tiretxn_detail", tid=tid))


@bp.route("/tire-transactions/<int:tid>/approve", methods=["POST"])
@login_required
@require_permission("tiretxn.view")
def tiretxn_approve(tid):
    try:
        TireTransactionService().approve(tid, user=current_user,
                                         remarks=request.form.get("remarks"))
        flash("Tire Transaction approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tiretxn_detail", tid=tid))


@bp.route("/tire-transactions/<int:tid>/reject", methods=["POST"])
@login_required
@require_permission("tiretxn.view")
def tiretxn_reject(tid):
    try:
        TireTransactionService().reject(tid, user=current_user,
                                        remarks=request.form.get("remarks"))
        flash("Tire Transaction rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tiretxn_detail", tid=tid))


@bp.route("/tire-transactions/<int:tid>/return", methods=["POST"])
@login_required
@require_permission("tiretxn.view")
def tiretxn_return(tid):
    try:
        TireTransactionService().return_document(
            tid, user=current_user, remarks=request.form.get("remarks"))
        flash("Tire Transaction returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tiretxn_detail", tid=tid))


@bp.route("/tire-transactions/<int:tid>/cancel", methods=["POST"])
@login_required
@require_permission("tiretxn.update")
def tiretxn_cancel(tid):
    try:
        TireTransactionService().cancel(tid, user=current_user)
        flash("Tire Transaction cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.tiretxn_detail", tid=tid))


@bp.route("/tire-transactions/<int:tid>/print")
@login_required
@require_permission("tiretxn.print")
def tiretxn_print(tid):
    item = db.session.get(TireTransaction, tid)
    return render_template("transactions/tiretxn_print.html", item=item,
                           **_print_report_context(item))


# ── Battery Transactions ─────────────────────────────────────────────────────

@bp.route("/battery-transactions")
@login_required
@require_permission("batterytxn.view")
def batterytxn_list():
    return render_template(
        "transactions/batterytxn_list.html",
        **_txn_list_context(BatteryTransactionService()))


@bp.route("/battery-transactions/new", methods=["GET", "POST"])
@login_required
@require_permission("batterytxn.create")
def batterytxn_new():
    from app.modules.master_data.battery.service import BatteryService
    batteries = BatteryService().list()
    if request.method == "POST":
        f = request.form
        try:
            BatteryTransactionService().create(
                battery_id=int(f["battery_id"]),
                vehicle_id=int(f["vehicle_id"]) if f.get("vehicle_id") else None,
                action=f["action"],
                transaction_date=parse_form_date(f.get("transaction_date"),
                                                 "Transaction Date", required=True),
                remarks=f.get("remarks"), user=current_user)
            flash("Battery Transaction recorded.", "success")
            return redirect(url_for("transactions.batterytxn_list"))
        except (InvalidBatteryActionError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/batterytxn_form.html",
                           batteries=batteries,
                           title="New Battery Transaction")


@bp.route("/battery-transactions/<int:bid>")
@login_required
@require_permission("batterytxn.view")
def batterytxn_detail(bid):
    item = BatteryTransactionService().get_visible(bid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/batterytxn_detail.html", item=item)


@bp.route("/battery-transactions/<int:bid>/submit", methods=["POST"])
@login_required
@require_permission("batterytxn.update")
def batterytxn_submit(bid):
    try:
        BatteryTransactionService().submit(bid, user=current_user)
        flash("Battery Transaction submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.batterytxn_detail", bid=bid))


@bp.route("/battery-transactions/<int:bid>/approve", methods=["POST"])
@login_required
@require_permission("batterytxn.view")
def batterytxn_approve(bid):
    try:
        BatteryTransactionService().approve(bid, user=current_user,
                                            remarks=request.form.get("remarks"))
        flash("Battery Transaction approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.batterytxn_detail", bid=bid))


@bp.route("/battery-transactions/<int:bid>/reject", methods=["POST"])
@login_required
@require_permission("batterytxn.view")
def batterytxn_reject(bid):
    try:
        BatteryTransactionService().reject(bid, user=current_user,
                                           remarks=request.form.get("remarks"))
        flash("Battery Transaction rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.batterytxn_detail", bid=bid))


@bp.route("/battery-transactions/<int:bid>/return", methods=["POST"])
@login_required
@require_permission("batterytxn.view")
def batterytxn_return(bid):
    try:
        BatteryTransactionService().return_document(
            bid, user=current_user, remarks=request.form.get("remarks"))
        flash("Battery Transaction returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.batterytxn_detail", bid=bid))


@bp.route("/battery-transactions/<int:bid>/cancel", methods=["POST"])
@login_required
@require_permission("batterytxn.update")
def batterytxn_cancel(bid):
    try:
        BatteryTransactionService().cancel(bid, user=current_user)
        flash("Battery Transaction cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.batterytxn_detail", bid=bid))


@bp.route("/battery-transactions/<int:bid>/print")
@login_required
@require_permission("batterytxn.print")
def batterytxn_print(bid):
    item = db.session.get(BatteryTransaction, bid)
    return render_template("transactions/batterytxn_print.html", item=item,
                           **_print_report_context(item))


# ── Purchase Requests ────────────────────────────────────────────────────────

@bp.route("/purchase-requests")
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_list():
    return render_template(
        "transactions/purchaserequest_list.html",
        **_txn_list_context(PurchaseRequestService()))


@bp.route("/purchase-requests/new", methods=["GET", "POST"])
@login_required
@require_permission("purchaserequest.create")
def purchaserequest_new():
    from app.modules.master_data.org.service import DepartmentService
    departments = DepartmentService().list()
    if request.method == "POST":
        f = request.form
        try:
            descs = f.getlist("item_description")
            qtys = f.getlist("quantity")
            costs = f.getlist("unit_cost")
            lines = [{"item_description": d, "quantity": float(q), "unit_cost": float(c)}
                     for d, q, c in zip(descs, qtys, costs) if d and q and c]
            PurchaseRequestService().create(
                description=f["description"], user=current_user, lines=lines,
                department_id=int(f["department_id"]) if f.get("department_id") else None,
                vendor_id=int(f["vendor_id"]) if f.get("vendor_id") else None,
                justification=f.get("justification"),
                needed_by_date=parse_form_date(f.get("needed_by_date"),
                                               "Needed By Date"))
            flash("Purchase Request created.", "success")
            return redirect(url_for("transactions.purchaserequest_list"))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/purchaserequest_form.html",
                           departments=departments,
                           title="New Purchase Request")


@bp.route("/purchase-requests/<int:pid>")
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_detail(pid):
    item = PurchaseRequestService().get_visible(pid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/purchaserequest_detail.html", item=item)


@bp.route("/purchase-requests/<int:pid>/print")
@login_required
@require_permission("purchaserequest.print")
def purchaserequest_print(pid):
    item = db.session.get(PurchaseRequest, pid)
    return render_template("transactions/purchaserequest_print.html",
                           item=item, **_print_report_context(item))


@bp.route("/purchase-requests/<int:pid>/submit", methods=["POST"])
@login_required
@require_permission("purchaserequest.update")
def purchaserequest_submit(pid):
    try:
        PurchaseRequestService().submit(pid, user=current_user)
        flash("Purchase Request submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/approve", methods=["POST"])
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_approve(pid):
    try:
        PurchaseRequestService().approve(pid, user=current_user,
                                         remarks=request.form.get("remarks"))
        flash("Purchase Request approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/reject", methods=["POST"])
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_reject(pid):
    try:
        PurchaseRequestService().reject(pid, user=current_user,
                                        remarks=request.form.get("remarks"))
        flash("Purchase Request rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/return", methods=["POST"])
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_return(pid):
    try:
        PurchaseRequestService().return_document(
            pid, user=current_user, remarks=request.form.get("remarks"))
        flash("Purchase Request returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/cancel", methods=["POST"])
@login_required
@require_permission("purchaserequest.update")
def purchaserequest_cancel(pid):
    try:
        PurchaseRequestService().cancel(pid, user=current_user)
        flash("Purchase Request cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/mark-ordered", methods=["POST"])
@login_required
@require_permission("purchaserequest.update")
def purchaserequest_mark_ordered(pid):
    PurchaseRequestService().mark_ordered(pid)
    flash("Purchase Request marked as ordered.", "success")
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


@bp.route("/purchase-requests/<int:pid>/mark-received", methods=["POST"])
@login_required
@require_permission("purchaserequest.update")
def purchaserequest_mark_received(pid):
    PurchaseRequestService().mark_received(pid)
    flash("Purchase Request marked as received.", "success")
    return redirect(url_for("transactions.purchaserequest_detail", pid=pid))


# ── Vehicle Registration ─────────────────────────────────────────────────────

@bp.route("/vehicle-registrations")
@login_required
@require_permission("vehicleregistration.view")
def vehicleregistration_list():
    return render_template(
        "transactions/vehicleregistration_list.html",
        **_txn_list_context(VehicleRegistrationService()))


@bp.route("/vehicle-registrations/new", methods=["GET", "POST"])
@login_required
@require_permission("vehicleregistration.create")
def vehicleregistration_new():
    from app.modules.master_data.vehicle.service import VehicleService as _VS
    prefill_vehicle = None
    if request.method == "GET" and request.args.get("vehicle_id"):
        prefill_vehicle = _VS().get(int(request.args["vehicle_id"]))
    prefill = {
        "registration_type": request.args.get("registration_type"),
        "registration_date": request.args.get("registration_date"),
    }
    if request.method == "POST":
        f = request.form
        try:
            VehicleRegistrationService().create(
                vehicle_id=int(f["vehicle_id"]),
                registration_type=f["registration_type"],
                registration_date=parse_form_date(f.get("registration_date"),
                                                  "Registration Date",
                                                  required=True),
                or_cr_cost=f.get("or_cr_cost") or None,
                odometer_at_registration=int(f["odometer_at_registration"]) if f.get("odometer_at_registration") else None,
                user=current_user)
            flash("Vehicle Registration created.", "success")
            return redirect(url_for("transactions.vehicleregistration_list"))
        except (DuplicateActiveRegistrationError,
                NoExistingRegistrationError, DateFormatError,
                RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/vehicleregistration_form.html",
                           prefill_vehicle=prefill_vehicle, prefill=prefill,
                           title="New Vehicle Registration")


@bp.route("/vehicle-registrations/<int:rid>")
@login_required
@require_permission("vehicleregistration.view")
def vehicleregistration_detail(rid):
    item = VehicleRegistrationService().get_visible(rid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/vehicleregistration_detail.html",
                           item=item)


@bp.route("/vehicle-registrations/<int:rid>/print")
@login_required
@require_permission("vehicleregistration.print")
def vehicleregistration_print(rid):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    from app.modules.master_data.routes import _attachment_rows
    from app.core.approval.engine import ApprovalEngine
    item = db.session.get(VehicleRegistration, rid)
    company = CompanyProfileService().get()
    attachments = _attachment_rows("vehicle_registrations", rid)
    approval_chain_data = (ApprovalEngine().get_approval_chain(item.approval_instance)
                           if item.approval_instance else [])
    return render_template("transactions/vehicleregistration_print.html",
                           item=item, company=company, attachments=attachments,
                           approval_chain_data=approval_chain_data)


@bp.route("/vehicle-registrations/<int:rid>/submit", methods=["POST"])
@login_required
@require_permission("vehicleregistration.update")
def vehicleregistration_submit(rid):
    try:
        VehicleRegistrationService().submit(rid, user=current_user)
        flash("Vehicle Registration submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/approve", methods=["POST"])
@login_required
@require_permission("vehicleregistration.view")
def vehicleregistration_approve(rid):
    try:
        VehicleRegistrationService().approve(rid, user=current_user,
                                             remarks=request.form.get("remarks"))
        flash("Vehicle Registration approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/reject", methods=["POST"])
@login_required
@require_permission("vehicleregistration.view")
def vehicleregistration_reject(rid):
    try:
        VehicleRegistrationService().reject(rid, user=current_user,
                                            remarks=request.form.get("remarks"))
        flash("Vehicle Registration rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/return", methods=["POST"])
@login_required
@require_permission("vehicleregistration.view")
def vehicleregistration_return(rid):
    try:
        VehicleRegistrationService().return_document(
            rid, user=current_user, remarks=request.form.get("remarks"))
        flash("Vehicle Registration returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/cancel", methods=["POST"])
@login_required
@require_permission("vehicleregistration.update")
def vehicleregistration_cancel(rid):
    try:
        VehicleRegistrationService().cancel(rid, user=current_user)
        flash("Vehicle Registration cancelled.", "info")
    except (InvalidStateError, NotVisibleError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/checklist/<int:item_id>/toggle", methods=["POST"])
@login_required
@require_permission("vehicleregistration.update")
def vehicleregistration_checklist_toggle(rid, item_id):
    done = request.form.get("done") == "1"
    try:
        VehicleRegistrationService().toggle_checklist_item(
            item_id, done=done, user=current_user)
    except InvalidRegistrationStateError as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


@bp.route("/vehicle-registrations/<int:rid>/complete", methods=["POST"])
@login_required
@require_permission("vehicleregistration.update")
def vehicleregistration_complete(rid):
    from app.modules.transactions.vehicle_registration.service import (
        RegistrationDateOrderError, DuplicateORNumberError,
        DuplicateCRNumberError)
    f = request.form
    try:
        VehicleRegistrationService().complete(
            rid, or_number=f["or_number"], cr_number=f["cr_number"],
            plate_number=f.get("plate_number") or None)
        flash("Vehicle Registration completed.", "success")
    except (RegistrationDateOrderError, DuplicateORNumberError,
           DuplicateCRNumberError) as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.vehicleregistration_detail", rid=rid))


# ── Maintenance Invoice & Actual Expense ────────────────────────────────────

@bp.route("/maintenance-orders/<int:oid>/invoices/new", methods=["GET", "POST"])
@login_required
@require_permission("maintenanceinvoice.create")
def maintenanceinvoice_new(oid):
    order = db.session.get(MaintenanceOrder, oid)
    if order is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        try:
            inv = MaintenanceInvoiceService().create(
                maintenance_order_id=oid, vendor_id=int(f["vendor_id"]),
                invoice_number=f["invoice_number"],
                invoice_date=parse_form_date(f.get("invoice_date"),
                                             "Invoice Date", required=True),
                vat_type=f.get("vat_type", "VAT_EXCLUSIVE"),
                vat_percentage=f.get("vat_percentage") or 12,
                or_number=f.get("or_number") or None,
                po_number=f.get("po_number") or None,
                dr_number=f.get("dr_number") or None,
                currency=f.get("currency", "PHP"), user=current_user)
            flash("Invoice created. Add line items below.", "success")
            return redirect(url_for("transactions.maintenanceinvoice_detail",
                                    iid=inv.id))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/maintenanceinvoice_form.html",
                           order=order, title="New Invoice")


@bp.route("/invoices/<int:iid>")
@login_required
@require_permission("maintenanceinvoice.view")
def maintenanceinvoice_detail(iid):
    from app.modules.system_admin.services.lookup_service import LookupService
    inv = MaintenanceInvoiceService().get_by_id(iid)
    if inv is None:
        abort(404)
    categories = LookupService().get_by_type_with_fallback("EXPENSE_CATEGORY")
    charge_tos = LookupService().get_by_type_with_fallback("CHARGE_TO")
    return render_template("transactions/maintenanceinvoice_detail.html",
                           item=inv, categories=categories, charge_tos=charge_tos)


def _invoice_totals_json(inv):
    """Recalculated totals for the AJAX line editor.

    The browser never computes money itself -- it displays what the
    server calculated. VAT, discount and rounding rules live in the
    service, and a second implementation in JavaScript would be a
    second place for them to drift.
    """
    return {
        "total_parts_cost": f"{inv.total_parts_cost:,.2f}",
        "total_labor_cost": f"{inv.total_labor_cost:,.2f}",
        "total_discount": f"{inv.total_discount:,.2f}",
        "net_amount": f"{inv.net_amount:,.2f}",
        "total_vat": f"{inv.total_vat:,.2f}",
        "gross_amount": f"{inv.gross_amount:,.2f}",
        "total_invoice_amount": f"{inv.total_invoice_amount:,.2f}",
    }


def _invoice_line_json(line):
    return {
        "id": line.id,
        "part_description": line.part_description,
        "part_number": line.part_number,
        "specification": line.specification,
        "uom": line.uom,
        "quantity": f"{line.quantity:,.2f}",
        "unit_cost": f"{line.unit_cost:,.2f}",
        "line_amount": f"{line.line_amount:,.2f}",
        "vat_amount": f"{line.vat_amount:,.2f}",
        "total_amount": f"{line.total_amount:,.2f}",
        "expense_category": line.expense_category,
        "charged_to": line.charged_to.replace("_", " ").title(),
    }


@bp.route("/invoices/<int:iid>/lines", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_add_line(iid):
    f = request.form
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        line = MaintenanceInvoiceService().add_line(
            iid, part_number=f.get("part_number") or None,
            part_description=f["part_description"],
            specification=f.get("specification") or None,
            uom=f.get("uom") or None,
            quantity=f.get("quantity") or 1,
            unit_cost=f.get("unit_cost") or 0,
            discount=f.get("discount") or 0,
            expense_category=f["expense_category"],
            charged_to=f["charged_to"])
        if wants_json:
            inv = MaintenanceInvoiceService().get_by_id(iid)
            return jsonify({"ok": True,
                           "line": _invoice_line_json(line),
                           "totals": _invoice_totals_json(inv)})
        flash("Line item added.", "success")
    except InvoiceLockedError as e:
        if wants_json:
            return jsonify({"ok": False, "error": str(e)}), 409
        flash(str(e), "danger")
    except (KeyError, ValueError, InvalidOperation) as e:
        # A bad number or a missing required field must come back as a
        # usable message next to the form, not a 500 that loses
        # everything the person had typed.
        #
        # InvalidOperation is listed explicitly because Decimal raises
        # it -- and it is NOT a subclass of ValueError, so catching
        # ValueError alone silently let a mistyped amount become a 500.
        if wants_json:
            return jsonify({"ok": False,
                           "error": f"Please check the values entered ({e})."}), 400
        flash("Please check the values entered.", "danger")
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/lines/<int:line_id>/delete", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_remove_line(iid, line_id):
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        MaintenanceInvoiceService().remove_line(line_id)
        if wants_json:
            inv = MaintenanceInvoiceService().get_by_id(iid)
            return jsonify({"ok": True,
                           "totals": _invoice_totals_json(inv)})
        flash("Line item removed.", "info")
    except InvoiceLockedError as e:
        if wants_json:
            return jsonify({"ok": False, "error": str(e)}), 409
        flash(str(e), "danger")
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/submit", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_submit(iid):
    try:
        MaintenanceInvoiceService().submit(iid, user=current_user)
        flash("Invoice submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/approve", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.view")
def maintenanceinvoice_approve(iid):
    try:
        MaintenanceInvoiceService().approve(iid, user=current_user,
                                            remarks=request.form.get("remarks"))
        inv = MaintenanceInvoiceService().get_by_id(iid)
        inv.status = "APPROVED"
        db.session.commit()
        flash("Invoice approved.", "success")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/reject", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.view")
def maintenanceinvoice_reject(iid):
    try:
        MaintenanceInvoiceService().reject(iid, user=current_user,
                                           remarks=request.form.get("remarks"))
        flash("Invoice rejected.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/return", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.view")
def maintenanceinvoice_return(iid):
    try:
        MaintenanceInvoiceService().return_document(
            iid, user=current_user, remarks=request.form.get("remarks"))
        flash("Invoice returned to requester.", "info")
    except (NotEligibleApproverError, InvalidStateError) as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/reopen", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_reopen(iid):
    """Explicit, authorized-only re-open of an APPROVED invoice for
    editing, per the spec: 'Prevent invoice modifications after approval
    unless reopened by an authorized user.'"""
    MaintenanceInvoiceService().reopen(iid, user=current_user)
    flash("Invoice reopened for editing.", "info")
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


# ── Fuel Management ─────────────────────────────────────────────────────────

@bp.route("/fuel")
@login_required
@require_permission("fuel.view")
def fuel_list():
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.analytics import (
        FuelAnalyticsService, FuelAnomalyCodes)
    from app.modules.master_data.org.service import BranchService

    view = request.args.get("view", "all")
    branch_id = request.args.get("branch_id", type=int)
    vehicle_status = request.args.get("vehicle_status", "")
    date_from = request.args.get("date_from", "")
    date_to = request.args.get("date_to", "")

    query = FuelTransaction.query
    if view == "flagged":
        # The exception queue -- where the money actually gets recovered.
        query = query.filter(db.or_(
            FuelTransaction.anomaly_flags.isnot(None),
            FuelTransaction.odometer_status.in_(("SUSPECT", "MISSING"))))
    if branch_id or vehicle_status:
        from app.modules.master_data.vehicle.models import Vehicle
        query = query.join(Vehicle, Vehicle.id == FuelTransaction.vehicle_id)
        if branch_id:
            query = query.filter(Vehicle.branch_id == branch_id)
        if vehicle_status:
            query = query.filter(Vehicle.status == vehicle_status)

    # Parsed defensively: a hand-edited or malformed date in the URL
    # must narrow the results silently, not 500 the whole page.
    if date_from:
        try:
            query = query.filter(FuelTransaction.transaction_date
                                >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            date_from = ""
    if date_to:
        try:
            # Inclusive of the whole end day, not just its midnight --
            # otherwise a transaction logged at 3pm on the end date
            # would be silently excluded from a range that names that
            # exact date.
            query = query.filter(
                FuelTransaction.transaction_date
                < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            date_to = ""

    rows = query.order_by(FuelTransaction.transaction_date.desc()).limit(500).all()
    svc = FuelAnalyticsService()
    return render_template("transactions/fuel_list.html", rows=rows,
                           view=view,
                           summary=svc.summary(),
                           exceptions=svc.exceptions_count(),
                           labels=FuelAnomalyCodes.LABELS,
                           selected_date_from=date_from,
                           selected_date_to=date_to,
                           branches=BranchService().list(),
                           selected_branch_id=branch_id,
                           selected_vehicle_status=vehicle_status)


@bp.route("/fuel/analytics")
@login_required
@require_permission("fuel.view")
def fuel_analytics():
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService
    svc = FuelAnalyticsService()
    return render_template("transactions/fuel_analytics.html",
                           summary=svc.summary(),
                           by_vehicle=svc.by_vehicle())


@bp.route("/fuel/new", methods=["GET", "POST"])
@login_required
@require_permission("fuel.create")
def fuel_new():
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.fuel.models import FuelCard, FuelTransaction
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    submitted = None
    if request.method == "POST":
        f = request.form
        try:
            from app.modules.transactions.fuel.import_export import (
                _to_decimal, _to_int, _to_datetime)
            txn = FuelTransaction(
                vehicle_id=int(f["vehicle_id"]),
                fuel_card_id=int(f["fuel_card_id"]) if f.get("fuel_card_id") else None,
                transaction_date=_to_datetime(f.get("transaction_date")),
                station=f.get("station") or None,
                fuel_type=(f.get("fuel_type") or "").upper() or None,
                litres=_to_decimal(f.get("litres")),
                price_per_litre=_to_decimal(f.get("price_per_litre")),
                total_amount=_to_decimal(f.get("total_amount")),
                odometer_reported=_to_int(f.get("odometer_reported")),
                reference_number=f.get("reference_number") or None,
                remarks=f.get("remarks") or None, source="MANUAL")
            db.session.add(txn)
            db.session.flush()
            OdometerValidationService().validate(txn)
            FuelAnalyticsService().detect_anomalies(txn)
            if txn.odometer_status in ("SUSPECT", "MISSING"):
                flash(f"Fuel transaction saved. {txn.odometer_note}",
                      "warning")
            else:
                flash("Fuel transaction saved.", "success")
            return redirect(url_for("transactions.fuel_list"))
        except Exception as exc:
            db.session.rollback()
            flash(f"Could not save: {exc}", "danger")
            submitted = f

    return render_template("transactions/fuel_form.html",
                           submitted=submitted,
                           vehicles=VehicleService().list(),
                           cards=FuelCard.query.filter_by(is_active=True).all())


@bp.route("/fuel/<int:tid>/accept-odometer", methods=["POST"])
@login_required
@require_permission("fuel.update")
def fuel_accept_odometer(tid):
    """A person confirms a flagged reading was genuinely correct."""
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        flash("Transaction not found.", "warning")
        return redirect(url_for("transactions.fuel_list"))
    OdometerValidationService().accept_reported(txn)
    flash("Reading confirmed. Consumption for this vehicle has been "
          "recalculated.", "success")
    return redirect(request.referrer or url_for("transactions.fuel_list"))


@bp.route("/fuel/<int:tid>/correct-odometer", methods=["POST"])
@login_required
@require_permission("fuel.update")
def fuel_correct_odometer(tid):
    """Enter the true reading. The reported value is never overwritten,
    so the original statement can still be reconciled."""
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        flash("Transaction not found.", "warning")
        return redirect(url_for("transactions.fuel_list"))
    try:
        corrected = int(request.form["odometer"])
    except (KeyError, ValueError):
        flash("Enter the corrected odometer as a whole number.", "danger")
        return redirect(request.referrer or url_for("transactions.fuel_list"))
    txn.odometer_used = corrected
    txn.odometer_status = "CORRECTED"
    txn.odometer_confirmed = True
    txn.odometer_note = (f"Corrected by a user from "
                        f"{txn.odometer_reported:,} to {corrected:,}."
                        if txn.odometer_reported else
                        f"Set by a user to {corrected:,}.")
    db.session.commit()
    OdometerValidationService().revalidate_vehicle(txn.vehicle_id)
    flash("Odometer corrected and consumption recalculated.", "success")
    return redirect(request.referrer or url_for("transactions.fuel_list"))


@bp.route("/fuel/template")
@login_required
@require_permission("fuel.create")
def fuel_template():
    from io import BytesIO
    from flask import send_file
    from app.modules.transactions.fuel.import_export import build_template
    return send_file(BytesIO(build_template()), as_attachment=True,
                     download_name="Fuel_Import_Template.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@bp.route("/fuel/export.xlsx")
@login_required
@require_permission("fuel.view")
def fuel_export():
    from io import BytesIO
    from flask import send_file
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.import_export import export_fuel
    rows = (FuelTransaction.query
           .order_by(FuelTransaction.transaction_date.desc()).all())
    return send_file(BytesIO(export_fuel(rows)), as_attachment=True,
                     download_name="Fuel_Transactions.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@bp.route("/fuel/import", methods=["GET", "POST"])
@login_required
@require_permission("fuel.create")
def fuel_import():
    from app.modules.transactions.fuel.import_export import (
        import_fuel, list_import_batches)
    stats = None
    if request.method == "POST":
        uploaded = request.files.get("file")
        if not uploaded or not uploaded.filename:
            flash("Choose a file to upload.", "warning")
        else:
            commit = request.form.get("commit") == "1"
            try:
                stats = import_fuel(uploaded.stream, dry_run=not commit,
                                   filename=uploaded.filename,
                                   user_id=current_user.id)
                if commit:
                    flash(f"Imported {stats['created']} transaction(s) from "
                         f"'{uploaded.filename}'.", "success")
            except ValueError as exc:
                flash(str(exc), "danger")
            except Exception as exc:
                flash(f"Could not read the file: {exc}", "danger")
    return render_template("transactions/fuel_import.html", stats=stats,
                           batches=list_import_batches())


@bp.route("/fuel/import-batches/<batch_id>/delete", methods=["POST"])
@login_required
@require_permission("fuel.delete")
def fuel_delete_batch(batch_id):
    """Undo one wrong upload -- removes exactly the rows that one file
    added, and recomputes consumption for any vehicle that had a later
    fill measuring from a reading this batch is removing.

    Gated on fuel.delete specifically, separate from fuel.create --
    the person who imports statements (a fleet analyst, an initiator)
    does not automatically need the ability to bulk-delete transaction
    history; that is a distinct, more consequential action a fleet
    admin grants deliberately.
    """
    from app.modules.transactions.fuel.import_export import (
        delete_import_batch)
    count = delete_import_batch(batch_id)
    if count:
        flash(f"Removed {count} transaction(s) from that upload.", "success")
    else:
        flash("That batch was not found -- it may already have been "
             "removed.", "warning")
    return redirect(url_for("transactions.fuel_import"))


# ── Maintenance Order: parts to procure ────────────────────────────────

def _mo_part_json(part):
    return {
        "id": part.id,
        "part_number": part.part_number,
        "part_description": part.part_description,
        "specification": part.specification,
        "uom": part.uom,
        "quantity": f"{part.quantity:,.2f}",
        "estimated_unit_cost": f"{part.estimated_unit_cost:,.2f}",
        "estimated_total": f"{part.estimated_total:,.2f}",
        "remarks": part.remarks,
    }


def _mo_parts_total(order):
    return f"{sum((p.estimated_total for p in order.parts), 0):,.2f}"


@bp.route("/maintenance-orders/<int:oid>/parts", methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_add_part(oid):
    f = request.form
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        part = MaintenanceOrderService().add_part(
            oid,
            part_description=f.get("part_description", ""),
            part_number=f.get("part_number") or None,
            specification=f.get("specification") or None,
            uom=f.get("uom") or None,
            quantity=f.get("quantity") or 1,
            estimated_unit_cost=f.get("estimated_unit_cost") or 0,
            remarks=f.get("remarks") or None)
        if wants_json:
            order = db.session.get(MaintenanceOrder, oid)
            return jsonify({"ok": True, "part": _mo_part_json(part),
                           "parts_total": _mo_parts_total(order)})
        flash("Part added.", "success")
    except InvalidOrderStateError as e:
        if wants_json:
            return jsonify({"ok": False, "error": str(e)}), 409
        flash(str(e), "danger")
    except (KeyError, ValueError, InvalidOperation) as e:
        if wants_json:
            return jsonify({"ok": False,
                           "error": f"Please check the values entered ({e})."}), 400
        flash("Please check the values entered.", "danger")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


@bp.route("/maintenance-orders/<int:oid>/parts/<int:part_id>/delete",
         methods=["POST"])
@login_required
@require_permission("maintenanceorder.update")
def maintenanceorder_remove_part(oid, part_id):
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    try:
        MaintenanceOrderService().remove_part(part_id)
        if wants_json:
            order = db.session.get(MaintenanceOrder, oid)
            return jsonify({"ok": True, "parts_total": _mo_parts_total(order)})
        flash("Part removed.", "info")
    except InvalidOrderStateError as e:
        if wants_json:
            return jsonify({"ok": False, "error": str(e)}), 409
        flash(str(e), "danger")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))


# ── Standard transaction list filtering ────────────────────────────────

def _txn_filters_from_request():
    """The standard filter set, parsed once so every transaction list
    reads its parameters identically."""
    from datetime import datetime

    def _date(name):
        raw = request.args.get(name)
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            # A malformed date in a hand-edited or stale URL must not
            # 500 the whole list -- it's simply ignored.
            return None

    f = {
        "q": request.args.get("q") or None,
        "status": request.args.get("status") or None,
        "branch_id": request.args.get("branch_id") or None,
        "date_from": _date("date_from"),
        "date_to": _date("date_to"),
        # Which date column the range applies to. Validated by the
        # service against its own declared columns, so an unrecognised
        # value from a stale bookmark falls back rather than raising.
        "date_field": request.args.get("date_field") or None,
    }
    f["has_any"] = any(v for k, v in f.items() if k != "has_any")
    return f


def _txn_list_context(service, *, extra_filters=None):
    """Runs the standard filtered query and returns everything the
    shared filter bar and pager templates need. Keeps each list route
    to a couple of lines rather than repeating this in every module."""
    from app.modules.master_data.org.models import Branch

    filters = _txn_filters_from_request()
    rows, pagination = service.list_filtered(
        user=current_user,
        page=request.args.get("page", 1, type=int),
        per_page=request.args.get("per_page", 25, type=int),
        search=filters["q"], status=filters["status"],
        branch_id=filters["branch_id"],
        date_from=filters["date_from"], date_to=filters["date_to"],
        date_field=filters["date_field"],
        extra_filters=extra_filters)
    # Echo back the field the service actually USED, not what was asked
    # for -- otherwise a bad value in the URL would leave the dropdown
    # showing a column the results were never filtered on.
    filters["date_field"] = service._resolve_date_field(
        filters["date_field"])
    return {
        "items": rows, "rows": rows, "pagination": pagination,
        "filters": filters,
        "date_field_choices": service.date_field_choices(),
        "status_choices": service.status_choices(),
        "branch_choices": Branch.query.filter_by(is_active=True)
                         .order_by(Branch.name).all(),
    }


@bp.route("/maintenance-orders/<int:oid>/generate-pr", methods=["POST"])
@login_required
@require_permission("purchaserequest.create")
def maintenanceorder_generate_pr(oid):
    """Generate the draft Purchase Request for an order that has parts
    but no PR yet.

    The automatic generation fires on the approval EVENT, so it only
    ever covers orders approved after that feature was installed. An
    order approved before then -- or one where generation failed and
    was logged rather than blocking the approval -- would otherwise be
    stuck with a parts list and no way to raise its PR without
    re-keying everything by hand, which is the exact duplication this
    was built to remove.

    Attributed to the order's own requester, not whoever clicks this,
    so the PR names the person who actually stated the requirement.
    """
    order = db.session.get(MaintenanceOrder, oid)
    if order is None:
        abort(404)
    if order.purchase_request_id:
        flash("This order already has a Purchase Request.", "info")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))
    if not order.parts:
        flash("Add at least one part to procure before generating a "
             "Purchase Request.", "warning")
        return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))
    try:
        pr = MaintenanceOrderService().generate_purchase_request(
            oid, user=order.requester)
        if pr:
            flash(f"Draft Purchase Request created. Review and submit it "
                 f"from the Purchase Request module.", "success")
        else:
            flash("Nothing to generate for this order.", "info")
    except Exception as exc:
        current_app.logger.exception(
            "Manual PR generation failed for maintenance order %s", oid)
        db.session.rollback()
        flash("Could not generate the Purchase Request. Please check the "
             "parts list and try again.", "danger")
    return redirect(url_for("transactions.maintenanceorder_detail", oid=oid))
