"""Transactions blueprint (Phase 3a): Trip Ticket, Authority To Drive,
Vehicle Movement. Thin controllers — all business logic lives in the
per-module services / the shared ApprovalEngine."""
from datetime import date, datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.core.approval.engine import (
    NotEligibleApproverError, InvalidStateError)
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

bp = Blueprint("transactions", __name__, url_prefix="/transactions",
               template_folder="templates")

for _mod in ["tripticket", "atd", "vehiclemovement"]:
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
    items = TripTicketService().list()
    return render_template("transactions/tripticket_list.html", items=items)


@bp.route("/trip-tickets/new", methods=["GET", "POST"])
@login_required
@require_permission("tripticket.create")
def tripticket_new():
    vehicles = VehicleService().list()
    drivers = DriverService().list()
    if request.method == "POST":
        f = request.form
        try:
            TripTicketService().create(
                vehicle_id=int(f["vehicle_id"]),
                driver_id=int(f["driver_id"]) if f.get("driver_id") else None,
                driver_name_manual=f.get("driver_name_manual") or None,
                destination=f["destination"], purpose=f["purpose"],
                departure_datetime=datetime.fromisoformat(f["departure_datetime"]),
                odometer_out=int(f["odometer_out"]) if f.get("odometer_out") else None,
                passengers=f.get("passengers"), user=current_user)
            flash("Trip Ticket created.", "success")
            return redirect(url_for("transactions.tripticket_list"))
        except DriverRequiredError as e:
            flash(str(e), "danger")
    return render_template("transactions/tripticket_form.html",
                           vehicles=vehicles, drivers=drivers,
                           title="New Trip Ticket")


@bp.route("/trip-tickets/<int:tid>")
@login_required
@require_permission("tripticket.view")
def tripticket_detail(tid):
    item = db.session.get(TripTicket, tid)
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
    except InvalidStateError as e:
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
    TripTicketService().complete(
        tid, odometer_in=int(f["odometer_in"]),
        return_datetime=datetime.fromisoformat(f["return_datetime"]))
    flash("Trip Ticket marked complete.", "success")
    return redirect(url_for("transactions.tripticket_detail", tid=tid))


# ── Authority To Drive ──────────────────────────────────────────────────────

@bp.route("/atd")
@login_required
@require_permission("atd.view")
def atd_list():
    items = ATDService().list()
    return render_template("transactions/atd_list.html", items=items)


@bp.route("/atd/new", methods=["GET", "POST"])
@login_required
@require_permission("atd.create")
def atd_new():
    vehicles = VehicleService().list()
    drivers = DriverService().list()
    if request.method == "POST":
        f = request.form
        ATDService().create(
            vehicle_id=int(f["vehicle_id"]), driver_id=int(f["driver_id"]),
            purpose=f["purpose"],
            valid_from=date.fromisoformat(f["valid_from"]),
            valid_to=date.fromisoformat(f["valid_to"]), user=current_user)
        flash("Authority To Drive created.", "success")
        return redirect(url_for("transactions.atd_list"))
    return render_template("transactions/atd_form.html", vehicles=vehicles,
                           drivers=drivers, title="New Authority To Drive")


@bp.route("/atd/<int:aid>")
@login_required
@require_permission("atd.view")
def atd_detail(aid):
    item = db.session.get(AuthorityToDrive, aid)
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
    except InvalidStateError as e:
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
    items = VehicleMovementService().list()
    return render_template("transactions/vehiclemovement_list.html", items=items)


@bp.route("/vehicle-movements/new", methods=["GET", "POST"])
@login_required
@require_permission("vehiclemovement.create")
def vehiclemovement_new():
    vehicles = VehicleService().list()
    if request.method == "POST":
        f = request.form
        try:
            VehicleMovementService().create(
                vehicle_id=int(f["vehicle_id"]),
                movement_type=f["movement_type"],
                from_location=f["from_location"],
                to_location=f["to_location"],
                movement_date=date.fromisoformat(f["movement_date"]),
                remarks=f.get("remarks"), user=current_user)
            flash("Vehicle Movement created.", "success")
            return redirect(url_for("transactions.vehiclemovement_list"))
        except InvalidMovementTypeError as e:
            flash(str(e), "danger")
    return render_template("transactions/vehiclemovement_form.html",
                           vehicles=vehicles, title="New Vehicle Movement")


@bp.route("/vehicle-movements/<int:mid>")
@login_required
@require_permission("vehiclemovement.view")
def vehiclemovement_detail(mid):
    item = db.session.get(VehicleMovement, mid)
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
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_submit(mid):
    try:
        VehicleMovementService().submit(mid, user=current_user)
        flash("Vehicle Movement submitted.", "success")
    except Exception as e:
        _flash_engine_error(e)
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))


@bp.route("/vehicle-movements/<int:mid>/cancel", methods=["POST"])
@login_required
@require_permission("vehiclemovement.update")
def vehiclemovement_cancel(mid):
    try:
        VehicleMovementService().cancel(mid, user=current_user)
        flash("Vehicle Movement cancelled.", "info")
    except InvalidStateError as e:
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
    VehicleMovementService().complete(mid)
    flash("Vehicle Movement completed.", "success")
    return redirect(url_for("transactions.vehiclemovement_detail", mid=mid))
