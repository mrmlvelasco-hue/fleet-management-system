"""Fuel transaction API — list, manual create, Excel template/import/export."""
from decimal import Decimal
from io import BytesIO

from flask import jsonify, request, send_file

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _iso(v):
    return v.isoformat() if v else None


def _num(v):
    if v is None:
        return None
    return float(v)


def _txn_json(t):
    v = t.vehicle
    return {
        "id": t.id,
        "document_number": t.document_number,
        "vehicle_id": t.vehicle_id,
        "fuel_card_id": t.fuel_card_id,
        "plate_number": v.plate_number if v else None,
        "vehicle_label": (
            " ".join(x for x in [getattr(v, "brand", None),
                                 getattr(v, "model", None)] if x) or None
        ) if v else None,
        "transaction_date": _iso(t.transaction_date),
        "station": t.station,
        "fuel_type": t.fuel_type,
        "litres": _num(t.litres),
        "price_per_litre": _num(t.price_per_litre),
        "total_amount": _num(t.total_amount),
        "odometer_reported": t.odometer_reported,
        "odometer_used": t.odometer_used,
        "odometer_status": t.odometer_status,
        "odometer_note": t.odometer_note,
        "distance_km": t.distance_km,
        "km_per_litre": _num(t.km_per_litre),
        "cost_per_km": _num(t.cost_per_km),
        "anomaly_flags": t.anomaly_flags,
        "reference_number": t.reference_number,
        "source": t.source,
        "remarks": t.remarks,
        "import_filename": t.import_filename,
        "import_batch_id": t.import_batch_id,
    }


def _stats_json(stats):
    if not stats:
        return None
    out = dict(stats)
    out["total_spend"] = _num(out.get("total_spend"))
    errors = []
    for e in out.get("errors") or []:
        errors.append({
            "row": e.get("row"),
            "identifier": e.get("identifier"),
            "problems": e.get("problems") or [],
        })
    out["errors"] = errors
    return out


@bp.route("/fuel", methods=["GET"])
@api_auth_required("fuel.view")
def list_fuel(api_user):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.analytics import (
        FuelAnalyticsService, fuel_scope_ids)
    from app.modules.master_data.vehicle.models import Vehicle

    from datetime import datetime, timedelta
    view = request.args.get("view") or "all"
    q = FuelTransaction.query
    if view == "flagged":
        q = q.filter(db.or_(
            FuelTransaction.anomaly_flags.isnot(None),
            FuelTransaction.odometer_status.in_(("SUSPECT", "MISSING"))))
    branch_id = request.args.get("branch_id", type=int)
    vehicle_status = request.args.get("vehicle_status") or ""
    date_from = request.args.get("date_from") or ""
    date_to = request.args.get("date_to") or ""

    # The acting user's OWN org scope decides what they can see at all;
    # an explicit branch_id filter can only narrow further, never reach
    # outside it.
    scope_ids = fuel_scope_ids(api_user, branch_id)

    if scope_ids is not None or vehicle_status:
        q = q.join(Vehicle, Vehicle.id == FuelTransaction.vehicle_id)
        if scope_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(scope_ids))
        if vehicle_status:
            q = q.filter(Vehicle.status == vehicle_status)
    if date_from:
        try:
            q = q.filter(FuelTransaction.transaction_date
                         >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(FuelTransaction.transaction_date
                         < datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            pass

    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 200)
    q = q.order_by(FuelTransaction.transaction_date.desc())
    pagination = q.paginate(page=page, per_page=page_size, error_out=False)
    # The KPI tiles beside the list must describe exactly the same rows
    # the list itself is scoped to -- never the whole fleet's figures.
    raw = FuelAnalyticsService().summary(branch_id=scope_ids)
    summary = {}
    for k, v in (raw or {}).items():
        if hasattr(v, "quantize"):
            summary[k] = float(v)
        else:
            summary[k] = v
    return jsonify({
        "items": [_txn_json(t) for t in pagination.items],
        "total": pagination.total,
        "page": page,
        "page_size": page_size,
        "pages": pagination.pages,
        "summary": summary,
    })


@bp.route("/fuel", methods=["POST"])
@api_auth_required("fuel.create")
def create_fuel(api_user):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.import_export import (
        _to_decimal, _to_int, _to_datetime)
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService

    p = request.get_json(silent=True) or {}
    if not p.get("vehicle_id"):
        return jsonify({"error": "validation", "message": "Vehicle is required.",
                        "fields": {"vehicle_id": "Required"}}), 400
    try:
        txn = FuelTransaction(
            vehicle_id=int(p["vehicle_id"]),
            fuel_card_id=int(p["fuel_card_id"]) if p.get("fuel_card_id") else None,
            transaction_date=_to_datetime(p.get("transaction_date")),
            station=p.get("station") or None,
            fuel_type=(p.get("fuel_type") or "").upper() or None,
            litres=_to_decimal(p.get("litres")),
            price_per_litre=_to_decimal(p.get("price_per_litre")),
            total_amount=_to_decimal(p.get("total_amount")),
            odometer_reported=_to_int(p.get("odometer_reported")),
            reference_number=p.get("reference_number") or None,
            remarks=p.get("remarks") or None,
            source="MANUAL",
        )
        db.session.add(txn)
        db.session.flush()
        OdometerValidationService().validate(txn)
        FuelAnalyticsService().detect_anomalies(txn)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": "validation", "message": str(exc)}), 400
    return jsonify(_txn_json(txn)), 201


@bp.route("/fuel/form-options", methods=["GET"])
@api_auth_required("fuel.view")
def fuel_form_options(api_user):
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.transactions.fuel.models import FuelCard
    vehicles = (Vehicle.query.filter(Vehicle.status != "DISPOSED")
                .order_by(Vehicle.plate_number).all())
    cards = (FuelCard.query.filter_by(status="ACTIVE")
             .order_by(FuelCard.card_number).all())
    return jsonify({
        "vehicles": [{
            "id": v.id,
            "plate_number": v.plate_number,
            "conduction_number": v.conduction_number,
            "brand": v.brand,
            "model": v.model,
        } for v in vehicles],
        "cards": [{
            "id": c.id,
            "card_number": c.card_number,
            "provider": c.provider,
            "vehicle_id": c.vehicle_id,
        } for c in cards],
        "fuel_types": ["DIESEL", "GASOLINE"],
    })


@bp.route("/fuel/<int:tid>", methods=["PUT", "PATCH"])
@api_auth_required("fuel.update")
def update_fuel(api_user, tid):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.import_export import (
        _to_decimal, _to_int, _to_datetime)
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    from app.modules.transactions.fuel.analytics import FuelAnalyticsService
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    p = request.get_json(silent=True) or {}
    try:
        if p.get("vehicle_id"):
            txn.vehicle_id = int(p["vehicle_id"])
        txn.fuel_card_id = int(p["fuel_card_id"]) if p.get("fuel_card_id") else None
        if p.get("transaction_date"):
            txn.transaction_date = _to_datetime(p.get("transaction_date"))
        txn.station = p.get("station") or None
        txn.fuel_type = (p.get("fuel_type") or "").upper() or None
        if p.get("litres") not in (None, ""):
            txn.litres = _to_decimal(p.get("litres"))
        txn.price_per_litre = _to_decimal(p.get("price_per_litre")) if p.get("price_per_litre") not in (None, "") else txn.price_per_litre
        if p.get("total_amount") not in (None, ""):
            txn.total_amount = _to_decimal(p.get("total_amount"))
        if "odometer_reported" in p:
            txn.odometer_reported = _to_int(p.get("odometer_reported"))
        txn.reference_number = p.get("reference_number") or None
        txn.remarks = p.get("remarks") or None
        db.session.flush()
        OdometerValidationService().validate(txn)
        FuelAnalyticsService().detect_anomalies(txn)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": "validation", "message": str(exc)}), 400
    return jsonify(_txn_json(txn))


@bp.route("/fuel/<int:tid>", methods=["GET"])
@api_auth_required("fuel.view")
def get_fuel(api_user, tid):
    from app.modules.transactions.fuel.models import FuelTransaction
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    return jsonify(_txn_json(txn))


@bp.route("/fuel/<int:tid>/accept-odometer", methods=["POST"])
@api_auth_required("fuel.update")
def accept_odometer(api_user, tid):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    OdometerValidationService().accept_reported(txn)
    return jsonify(_txn_json(txn))


@bp.route("/fuel/<int:tid>/correct-odometer", methods=["POST"])
@api_auth_required("fuel.update")
def correct_odometer(api_user, tid):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.odometer_validation import (
        OdometerValidationService)
    txn = db.session.get(FuelTransaction, tid)
    if txn is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    p = request.get_json(silent=True) or {}
    try:
        corrected = int(p["odometer"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "validation",
                        "message": "Enter the corrected odometer as a whole number."}), 400
    txn.odometer_used = corrected
    txn.odometer_status = "CORRECTED"
    txn.odometer_confirmed = True
    txn.odometer_note = (
        f"Corrected by a user from {txn.odometer_reported:,} to {corrected:,}."
        if txn.odometer_reported else f"Set by a user to {corrected:,}.")
    db.session.commit()
    OdometerValidationService().revalidate_vehicle(txn.vehicle_id)
    return jsonify(_txn_json(txn))


@bp.route("/fuel/template", methods=["GET"])
@api_auth_required("fuel.create")
def fuel_template(api_user):
    from app.modules.transactions.fuel.import_export import build_template
    return send_file(
        BytesIO(build_template()),
        as_attachment=True,
        download_name="Fuel_Import_Template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/fuel/export.xlsx", methods=["GET"])
@api_auth_required("fuel.view")
def fuel_export(api_user):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.fuel.import_export import export_fuel
    from app.modules.transactions.fuel.analytics import fuel_scope_ids
    from app.modules.master_data.vehicle.models import Vehicle

    q = FuelTransaction.query
    scope_ids = fuel_scope_ids(
        api_user, request.args.get("branch_id", type=int))
    if scope_ids is not None:
        # Same rule as the list endpoint: an export must never hand a
        # branch-restricted user a spreadsheet of branches their own
        # scope doesn't cover.
        q = q.join(Vehicle, Vehicle.id == FuelTransaction.vehicle_id) \
             .filter(Vehicle.branch_id.in_(scope_ids))
    rows = q.order_by(FuelTransaction.transaction_date.desc()).all()
    return send_file(
        BytesIO(export_fuel(rows)),
        as_attachment=True,
        download_name="Fuel_Transactions.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _analysis_filters():
    return {
        "year": request.args.get("year", type=int),
        "branch_id": request.args.get("branch_id", type=int),
        "department_id": request.args.get("department_id", type=int),
        "vehicle_type_id": request.args.get("vehicle_type_id", type=int),
        "fuel_type": request.args.get("fuel_type") or None,
        "station": request.args.get("station") or None,
        "plate": request.args.get("plate") or None,
    }


def _month_json(m):
    return {
        "month": m["month"],
        "label": m["label"],
        "end_odometer": m["end_odometer"],
        "km_driven": m["km_driven"],
        "litres": _num(m["litres"]),
        "total_cost": _num(m["total_cost"]),
        "km_per_litre": _num(m["km_per_litre"]),
        "cost_per_km": _num(m["cost_per_km"]),
        "reason": m["reason"],
    }


def _analysis_row_json(row):
    return {
        "vehicle_id": row["vehicle_id"],
        "plate_number": row["plate_number"],
        "vehicle_label": row["vehicle_label"],
        "branch_id": row["branch_id"],
        "branch_name": row["branch_name"],
        "department_id": row["department_id"],
        "department_name": row["department_name"],
        "months": [_month_json(m) for m in row["months"]],
    }


def _analysis_summary_json(summary):
    out = dict(summary)
    for k in ("total_litres", "total_cost", "km_per_litre", "cost_per_km"):
        out[k] = _num(out.get(k))
    return out


@bp.route("/fuel/analysis", methods=["GET"])
@api_auth_required("fuel.view")
def fuel_analysis(api_user):
    from app.modules.transactions.fuel.monthly_analysis import (
        FuelMonthlyAnalysisService)

    filters = _analysis_filters()
    svc = FuelMonthlyAnalysisService()
    rows = svc.monthly_rows(user=api_user, **filters)
    year = filters.pop("year", None)
    summary = svc.fleet_summary(year=year, user=api_user, **filters)
    return jsonify({
        "rows": [_analysis_row_json(r) for r in rows],
        "summary": _analysis_summary_json(summary),
    })


@bp.route("/fuel/analysis/export.xlsx", methods=["GET"])
@api_auth_required("fuel.view")
def fuel_analysis_export(api_user):
    from app.modules.transactions.fuel.monthly_analysis import (
        FuelMonthlyAnalysisService, export_monthly_analysis_xlsx)

    filters = _analysis_filters()
    svc = FuelMonthlyAnalysisService()
    rows = svc.monthly_rows(user=api_user, **filters)
    year = filters.pop("year", None)
    summary = svc.fleet_summary(year=year, user=api_user, **filters)
    xlsx = export_monthly_analysis_xlsx(rows, year=year, fleet_summary=summary)
    return send_file(
        BytesIO(xlsx),
        as_attachment=True,
        download_name=f"Fuel_Analysis_{summary['year']}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/fuel/import", methods=["POST"])
@api_auth_required("fuel.create")
def fuel_import(api_user):
    from app.modules.transactions.fuel.import_export import import_fuel
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "validation",
                        "message": "Choose a file to upload."}), 400
    commit = request.form.get("commit") == "1"
    try:
        stats = import_fuel(
            uploaded.stream, dry_run=not commit,
            filename=uploaded.filename, user_id=api_user.id)
    except ValueError as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "validation",
                        "message": f"Could not read the file: {exc}"}), 400
    return jsonify(_stats_json(stats))


@bp.route("/fuel/import-batches", methods=["GET"])
@api_auth_required("fuel.view")
def fuel_batches(api_user):
    from app.modules.transactions.fuel.import_export import list_import_batches
    rows = list_import_batches()
    items = []
    for r in rows:
        who = r.get("imported_by")
        items.append({
            "batch_id": r["batch_id"],
            "filename": r["filename"],
            "imported_by": getattr(who, "full_name", None) or getattr(who, "username", None),
            "imported_at": _iso(r.get("imported_at")),
            "row_count": r["row_count"],
            "total_spend": _num(r["total_spend"]),
        })
    return jsonify({"items": items})


@bp.route("/fuel/import-batches/<batch_id>", methods=["DELETE"])
@api_auth_required("fuel.delete")
def fuel_delete_batch(api_user, batch_id):
    from app.modules.transactions.fuel.import_export import delete_import_batch
    count = delete_import_batch(batch_id)
    return jsonify({"deleted": count})
