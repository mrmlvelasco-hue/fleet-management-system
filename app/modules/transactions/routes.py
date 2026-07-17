"""Transactions blueprint (Phase 3a): Trip Ticket, Authority To Drive,
Vehicle Movement. Thin controllers — all business logic lives in the
per-module services / the shared ApprovalEngine."""
from datetime import date, datetime

from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, abort)
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
    MaintenanceOrderService, IncompleteChecklistError, InvalidOrderStateError)
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
    NoExistingRegistrationError)
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)

bp = Blueprint("transactions", __name__, url_prefix="/transactions",
               template_folder="templates")

for _mod in ["tripticket", "atd", "vehiclemovement", "maintenanceorder",
             "tiretxn", "batterytxn", "purchaserequest", "vehicleregistration",
             "maintenanceinvoice"]:
    for _act in ["view", "create", "update", "delete", "print"]:
        _code = f"{_mod}.{_act}"
        registry.register(_code, _mod, _act, f"{_act.title()} {_mod}")


def _flash_engine_error(exc):
    flash(str(exc), "danger")


# ── Trip Tickets ────────────────────────────────────────────────────────────

@bp.route("/trip-tickets")
@login_required
@require_permission("tripticket.view")
def tripticket_list():
    items = TripTicketService().list(user=current_user)
    return render_template("transactions/tripticket_list.html", items=items)


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(TripTicket, tid)
    company = CompanyProfileService().get()
    return render_template("transactions/tripticket_print.html", item=item,
                           company=company)


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
    items = ATDService().list(user=current_user)
    return render_template("transactions/atd_list.html", items=items)


@bp.route("/atd/new", methods=["GET", "POST"])
@login_required
@require_permission("atd.create")
def atd_new():
    if request.method == "POST":
        f = request.form
        try:
            ATDService().create(
                vehicle_id=int(f["vehicle_id"]), driver_id=int(f["driver_id"]),
                purpose=f["purpose"],
                valid_from=parse_form_date(f.get("valid_from"), "Valid From",
                                           required=True),
                valid_to=parse_form_date(f.get("valid_to"), "Valid To",
                                         required=True), user=current_user)
            flash("Authority To Drive created.", "success")
            return redirect(url_for("transactions.atd_list"))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/atd_form.html",
                           title="New Authority To Drive")


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(AuthorityToDrive, aid)
    company = CompanyProfileService().get()
    return render_template("transactions/atd_print.html", item=item,
                           company=company)


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
    items = VehicleMovementService().list(user=current_user)
    return render_template("transactions/vehiclemovement_list.html", items=items)


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(VehicleMovement, mid)
    company = CompanyProfileService().get()
    return render_template("transactions/vehiclemovement_print.html",
                           item=item, company=company)


@bp.route("/vehicle-movements/<int:mid>/submit", methods=["POST"])
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
    items = MaintenanceOrderService().list(user=current_user)
    return render_template("transactions/maintenanceorder_list.html", items=items)


@bp.route("/maintenance-orders/new", methods=["GET", "POST"])
@login_required
@require_permission("maintenanceorder.create")
def maintenanceorder_new():
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.maintenance_config.service import (
        PMScheduleService, PMScopeTemplateService)

    maintenance_types = MaintenanceTypeService().list()
    scope_templates = PMScopeTemplateService().list()

    # Optional pre-fill via query params — used by the Dashboard's
    # "Vehicles Due for Maintenance" widget so clicking a due item goes
    # straight into a ready-to-submit order instead of just the vehicle's
    # detail page.
    prefill_vehicle = None
    if request.method == "GET" and request.args.get("vehicle_id"):
        prefill_vehicle = VehicleService().get(int(request.args["vehicle_id"]))
    prefill = {
        "vehicle_id": request.args.get("vehicle_id", type=int),
        "maintenance_type_id": request.args.get("maintenance_type_id", type=int),
        "scope_template_id": request.args.get("scope_template_id", type=int),
        "odometer_at_service": request.args.get("odometer_at_service", type=int),
        "scheduled_date": request.args.get("scheduled_date"),
    }

    if request.method == "POST":
        f = request.form
        try:
            MaintenanceOrderService().create(
                vehicle_id=int(f["vehicle_id"]),
                maintenance_type_id=int(f["maintenance_type_id"]),
                scope_template_id=int(f["scope_template_id"]) if f.get("scope_template_id") else None,
                scheduled_date=parse_form_date(f.get("scheduled_date"),
                                               "Scheduled Date", required=True),
                odometer_at_service=int(f["odometer_at_service"]) if f.get("odometer_at_service") else None,
                description=f.get("description"),
                assigned_mechanic=f.get("assigned_mechanic"),
                vendor_id=int(f["vendor_id"]) if f.get("vendor_id") else None,
                estimated_cost=f.get("estimated_cost") or None,
                user=current_user)
            flash("Maintenance Order created.", "success")
            return redirect(url_for("transactions.maintenanceorder_list"))
        except (DateFormatError, RequiredFieldError) as e:
            flash(str(e), "danger")
    return render_template("transactions/maintenanceorder_form.html",
                           maintenance_types=maintenance_types,
                           scope_templates=scope_templates,
                           prefill_vehicle=prefill_vehicle, prefill=prefill,
                           title="New Maintenance Order")


@bp.route("/maintenance-orders/<int:oid>")
@login_required
@require_permission("maintenanceorder.view")
def maintenanceorder_detail(oid):
    item = MaintenanceOrderService().get_visible(oid, current_user)
    if item is None:
        abort(403)
    return render_template("transactions/maintenanceorder_detail.html", item=item)


@bp.route("/maintenance-orders/<int:oid>/print")
@login_required
@require_permission("maintenanceorder.print")
def maintenanceorder_print(oid):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(MaintenanceOrder, oid)
    company = CompanyProfileService().get()
    return render_template("transactions/maintenanceorder_print.html",
                           item=item, company=company)


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
    items = TireTransactionService().list(user=current_user)
    return render_template("transactions/tiretxn_list.html", items=items)


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(TireTransaction, tid)
    company = CompanyProfileService().get()
    return render_template("transactions/tiretxn_print.html", item=item,
                           company=company)


# ── Battery Transactions ─────────────────────────────────────────────────────

@bp.route("/battery-transactions")
@login_required
@require_permission("batterytxn.view")
def batterytxn_list():
    items = BatteryTransactionService().list(user=current_user)
    return render_template("transactions/batterytxn_list.html", items=items)


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(BatteryTransaction, bid)
    company = CompanyProfileService().get()
    return render_template("transactions/batterytxn_print.html", item=item,
                           company=company)


# ── Purchase Requests ────────────────────────────────────────────────────────

@bp.route("/purchase-requests")
@login_required
@require_permission("purchaserequest.view")
def purchaserequest_list():
    items = PurchaseRequestService().list(user=current_user)
    return render_template("transactions/purchaserequest_list.html", items=items)


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
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    item = db.session.get(PurchaseRequest, pid)
    company = CompanyProfileService().get()
    return render_template("transactions/purchaserequest_print.html",
                           item=item, company=company)


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
    items = VehicleRegistrationService().list(user=current_user)
    return render_template("transactions/vehicleregistration_list.html",
                           items=items)


@bp.route("/vehicle-registrations/new", methods=["GET", "POST"])
@login_required
@require_permission("vehicleregistration.create")
def vehicleregistration_new():
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
    item = db.session.get(VehicleRegistration, rid)
    company = CompanyProfileService().get()
    return render_template("transactions/vehicleregistration_print.html",
                           item=item, company=company)


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


@bp.route("/vehicle-registrations/<int:rid>/complete", methods=["POST"])
@login_required
@require_permission("vehicleregistration.update")
def vehicleregistration_complete(rid):
    f = request.form
    VehicleRegistrationService().complete(
        rid, or_number=f["or_number"], cr_number=f["cr_number"],
        plate_number=f.get("plate_number") or None)
    flash("Vehicle Registration completed.", "success")
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


@bp.route("/invoices/<int:iid>/lines", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_add_line(iid):
    f = request.form
    try:
        MaintenanceInvoiceService().add_line(
            iid, part_number=f.get("part_number") or None,
            part_description=f["part_description"],
            specification=f.get("specification") or None,
            uom=f.get("uom") or None,
            quantity=f.get("quantity") or 1,
            unit_cost=f.get("unit_cost") or 0,
            discount=f.get("discount") or 0,
            expense_category=f["expense_category"],
            charged_to=f["charged_to"])
        flash("Line item added.", "success")
    except InvoiceLockedError as e:
        flash(str(e), "danger")
    return redirect(url_for("transactions.maintenanceinvoice_detail", iid=iid))


@bp.route("/invoices/<int:iid>/lines/<int:line_id>/delete", methods=["POST"])
@login_required
@require_permission("maintenanceinvoice.update")
def maintenanceinvoice_remove_line(iid, line_id):
    try:
        MaintenanceInvoiceService().remove_line(line_id)
        flash("Line item removed.", "info")
    except InvoiceLockedError as e:
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
