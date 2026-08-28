"""Maintenance Order API for React — list, detail, create, lifecycle, checklist, parts."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="Maintenance Order"):
    return jsonify({"error": "not_found", "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _iso(d):
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


def _dec(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _attachment_count(order_id):
    from app.core.models.attachment import Attachment
    return Attachment.query.filter_by(
        reference_table="maintenance_orders", reference_id=order_id).count()



def _display_status(order):
    """User-facing status combining physical + approval state."""
    phys = (order.status or "").upper()
    inst = getattr(order, "approval_instance", None)
    appr = (inst.status if inst is not None else None)
    if phys == "CANCELLED":
        return "Cancelled"
    if phys == "COMPLETED":
        return "Completed"
    if phys == "IN_PROGRESS":
        return "In Progress"
    if appr == "PENDING":
        return "Awaiting Approval"
    if appr == "RETURNED":
        return "Returned for Revision"
    if appr == "REJECTED":
        return "Rejected"
    if appr == "APPROVED" and phys == "DRAFT":
        return "Approved"
    if phys == "DRAFT":
        return "Draft"
    return phys.title() if phys else "Draft"


def _approval_audit(order):
    inst = getattr(order, "approval_instance", None)
    if inst is None:
        return []
    from app.modules.user_management.models import User
    rows = []
    for a in inst.actions or []:
        from app.extensions import db as _db
        actor = _db.session.get(User, a.acted_by) if a.acted_by else None
        rows.append({
            "action": a.action,
            "level_number": a.level_number,
            "acted_by_name": actor.full_name if actor else None,
            "acted_at": a.acted_at.isoformat() if a.acted_at else None,
            "remarks": a.remarks,
        })
    return rows


def _vam_print_extras(o):
    """Photos, registration numbers and endorsement block for VAM print."""
    extras = {
        "driver_photo_attachment_id": None,
        "driver_position": None,
        "driver_address": None,
        "driver_phone": None,
        "vehicle_cr_number": None,
        "registration_cr_number": None,
        "registration_or_number": None,
        "photo_front_id": None,
        "photo_back_id": None,
        "endorsed_by": None,
        "approved_by": None,
        "vehicle_brand_new": None,
    }
    drv = getattr(o, "driver", None)
    if drv is not None:
        extras["driver_photo_attachment_id"] = getattr(drv, "photo_attachment_id", None)
        extras["driver_position"] = (
            getattr(drv, "position", None) or getattr(drv, "job_title", None))
        extras["driver_address"] = getattr(drv, "complete_address", None)
        extras["driver_phone"] = getattr(drv, "phone", None)
    veh = getattr(o, "vehicle", None)
    if veh is not None:
        extras["vehicle_cr_number"] = getattr(veh, "cr_number", None)
        odo = getattr(veh, "current_odometer", None)
        extras["vehicle_brand_new"] = "Yes" if odo is not None and odo < 500 else "No"
        from app.core.models.attachment import Attachment
        for code, key in (("PHOTO_FRONT", "photo_front_id"),
                          ("PHOTO_BACK", "photo_back_id")):
            att = (Attachment.query
                   .filter_by(reference_table="vehicles",
                              reference_id=veh.id,
                              document_type=code, is_active=True)
                   .order_by(Attachment.created_at.desc())
                   .first())
            if att:
                extras[key] = att.id
        try:
            from app.modules.transactions.vehicle_registration.models import (
                VehicleRegistration)
            latest = (VehicleRegistration.query
                      .filter_by(vehicle_id=veh.id, status="COMPLETED")
                      .order_by(VehicleRegistration.expiry_date.desc())
                      .first())
            if latest:
                extras["registration_cr_number"] = getattr(latest, "cr_number", None)
                extras["registration_or_number"] = getattr(latest, "or_number", None)
        except Exception:
            pass
    if getattr(o, "requester", None):
        role = o.requester.roles[0].name if o.requester.roles else ""
        extras["endorsed_by"] = {
            "name": o.requester.full_name,
            "title": role,
            "date": (o.created_at.date().isoformat()
                     if getattr(o, "created_at", None) else None),
        }
    inst = getattr(o, "approval_instance", None)
    if inst is not None:
        from app.core.approval.models import ApprovalTask
        from app.modules.user_management.models import User
        from app.extensions import db
        task = (ApprovalTask.query
                .filter_by(approval_instance_id=inst.id, status="COMPLETED")
                .order_by(ApprovalTask.level_number.desc())
                .first())
        if task and task.completed_by:
            approver = db.session.get(User, task.completed_by)
            if approver:
                extras["approved_by"] = {
                    "name": approver.full_name,
                    "title": approver.roles[0].name if approver.roles else "",
                    "date": (task.completed_at.date().isoformat()
                             if task.completed_at else None),
                }
    return extras

def _order_json(o, *, detail=False):
    plate = None
    if o.vehicle:
        plate = o.vehicle.plate_number or o.vehicle.conduction_number
    data = {
        "id": o.id,
        "document_number": o.document_number,
        "status": o.status,
        "display_status": _display_status(o),
        "approval_status": (
            o.approval_instance.status if getattr(o, "approval_instance", None) else None),
        "order_category": o.order_category,
        "vehicle_id": o.vehicle_id,
        "plate_number": plate,
        "vehicle_label": (
            f"{o.vehicle.brand or ''} {o.vehicle.model or ''}".strip()
            if o.vehicle else None),
        "maintenance_type_id": o.maintenance_type_id,
        "maintenance_type": (
            o.maintenance_type.name if o.maintenance_type else None),
        "transaction_type_id": o.transaction_type_id,
        "transaction_type": (
            o.transaction_type.name if o.transaction_type else None),
        "transaction_type_code": (
            o.transaction_type.code if o.transaction_type else None),
        "transaction_type_group": (
            o.transaction_type.group if o.transaction_type else None),
        "print_template": __import__(
            "app.modules.transactions.maintenance_order.service",
            fromlist=["resolve_print_template"]
        ).resolve_print_template(o.transaction_type),
        "print_label": __import__(
            "app.modules.transactions.maintenance_order.service",
            fromlist=["PRINT_TEMPLATE_LABELS", "resolve_print_template"]
        ).PRINT_TEMPLATE_LABELS.get(
            __import__(
                "app.modules.transactions.maintenance_order.service",
                fromlist=["resolve_print_template"]
            ).resolve_print_template(o.transaction_type),
            "Work Order"),
        "maintenance_class_label": getattr(o, "maintenance_class_label", None),
        "scheduled_date": _iso(o.scheduled_date),
        "completed_date": _iso(o.completed_date),
        "odometer_at_service": o.odometer_at_service,
        # Feeds the New Invoice Header's Vehicle summary card. Cheap
        # additions from relations the Vehicle model already has -- no
        # new query, no schema change. None rather than a fabricated
        # placeholder: a vehicle with no branch or driver assigned
        # should say so, not show a made-up default.
        "vehicle_branch": (o.vehicle.branch.name
                           if o.vehicle and o.vehicle.branch else None),
        "vehicle_assigned_driver": (
            o.vehicle.assigned_driver.full_name
            if o.vehicle and o.vehicle.assigned_driver else None),
        "driver": (o.driver.full_name if getattr(o, "driver", None) else None),
        "assignment_classification": o.assignment_classification,
        "vehicle_current_odometer": (
            o.vehicle.current_odometer if o.vehicle else None),
        # PR / Invoice column. purchase_request_id already exists on
        # the model; invoices are already linked via backref. None
        # rather than a placeholder string -- most orders never
        # generate either, and deciding what "none" looks like (an em
        # dash, blank, etc.) is a presentation choice for the client,
        # not something the API should bake in.
        "pr_id": o.purchase_request_id,
        "pr_document_number": (
            o.purchase_request.document_number
            if getattr(o, "purchase_request", None) else None),
        "invoice_document_number": (
            sorted(o.invoices, key=lambda i: i.id)[-1].document_number
            if getattr(o, "invoices", None) else None),
        # Docs column. Attachment is the same generic reference-table /
        # reference-id model used everywhere else; no new mechanism.
        # Zero is the honest count when there are none -- a missing key
        # would make the client guess whether zero or "not loaded" is
        # meant.
        "attachment_count": _attachment_count(o.id),
        "requested_by": o.requester.username if o.requester else None,
        "estimated_cost": _dec(o.estimated_cost),
        "actual_cost": _dec(o.actual_cost),
        "assigned_mechanic": o.assigned_mechanic,
        "vendor_id": o.vendor_id,
        "vendor": o.vendor.name if getattr(o, "vendor", None) else None,
        "description": o.description,
        "created_at": _iso(getattr(o, "created_at", None)),
    }
    if detail:
        data.update({
            "scope_template_id": o.scope_template_id,
            "pm_schedule_id": o.pm_schedule_id,
            "driver_id": o.driver_id,
            "driver": (
                o.driver.full_name if getattr(o, "driver", None) else None),
            "destination_branch_id": o.destination_branch_id,
            "origin_branch_id": o.origin_branch_id,
            "assignment_classification": o.assignment_classification,
            "driver_employee_number": (
                o.driver.employee_number if getattr(o, "driver", None) else None),
            "destination_branch": (
                o.destination_branch.name
                if getattr(o, "destination_branch", None) else None),
            "origin_branch": (
                o.origin_branch.name if getattr(o, "origin_branch", None)
                else (o.vehicle.branch.name
                      if o.vehicle and getattr(o.vehicle, "branch", None)
                      else None)),
            "vehicle_year": getattr(o.vehicle, "year", None) if o.vehicle else None,
            "vehicle_engine_number": (
                getattr(o.vehicle, "engine_number", None) if o.vehicle else None),
            "vehicle_chassis_number": (
                getattr(o.vehicle, "chassis_number", None) if o.vehicle else None),
            "vehicle_color": getattr(o.vehicle, "color", None) if o.vehicle else None,
            "vehicle_far_number": (
                getattr(o.vehicle, "far_number", None) if o.vehicle else None),
            "vehicle_odometer": (
                getattr(o.vehicle, "current_odometer", None) if o.vehicle else None),
            "assignee_name": (
                o.driver.full_name if getattr(o, "driver", None)
                else (o.vehicle.assigned_driver.full_name
                      if o.vehicle and getattr(o.vehicle, "assigned_driver", None)
                      else None)),
            "disposal_value": _dec(o.disposal_value),
            "disposal_recipient": o.disposal_recipient,
            "disposal_reference_number": o.disposal_reference_number,
            "transfer_reference_number": o.transfer_reference_number,
            "checklist_items": [
                {
                    "id": i.id,
                    "activity_code": i.activity_code,
                    "activity_description": i.activity_description,
                    "is_done": bool(i.is_done),
                    "sort_order": i.sort_order,
                    "done_at": _iso(i.done_at),
                }
                for i in (o.checklist_items or [])
            ],
            "parts": [
                {
                    "id": p.id,
                    "part_number": p.part_number,
                    "part_description": p.part_description,
                    "specification": p.specification,
                    "uom": p.uom,
                    "quantity": _dec(p.quantity),
                    "estimated_unit_cost": _dec(p.estimated_unit_cost),
                    "estimated_total": _dec(p.estimated_total),
                    "remarks": p.remarks,
                }
                for p in (getattr(o, "parts", None) or [])
            ],
            "editable": None,  # filled by caller with service
        })
    return data


@bp.route("/maintenance-orders", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def list_maintenance_orders_v2(api_user):

    def _inbox_keep(o, inbox):
        if not inbox:
            return True
        inst = getattr(o, "approval_instance", None)
        appr = inst.status if inst is not None else None
        uid = getattr(api_user, "id", None)
        if inbox in ("mine", "my-requests"):
            return getattr(o, "requested_by", None) == uid
        if inbox in ("returned", "returned-requests"):
            return appr == "RETURNED" and getattr(o, "requested_by", None) == uid
        if inbox in ("awaiting-action", "awaiting"):
            return appr == "RETURNED" and getattr(o, "requested_by", None) == uid
        if inbox in ("awaiting-approval",):
            return appr == "PENDING"
        return True

    """Paginated list. Replaces the thin results shape used by the dashboard
    widget with a full list payload for the React screen."""
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 25))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    page = max(1, page)
    page_size = max(1, min(page_size, 100))

    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().upper() or None
    cls = (request.args.get("maintenance_class") or "").strip().upper() or None
    inbox = (request.args.get("inbox") or "").strip().lower() or None

    svc = MaintenanceOrderService()
    extra = []
    if cls:
        extra.append(svc.maintenance_class_clause(cls))

    # Prefer list_filtered when available
    if hasattr(svc, "list_filtered"):
        rows, pagination = svc.list_filtered(
            user=api_user, page=page, per_page=page_size,
            search=q or None, status=status,
            extra_filters=extra or None)
        total = pagination.total
        items = [_order_json(o) for o in rows]
        return jsonify({
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, pagination.pages or 1),
            "count": total,
            "results": items,
        })

    orders = svc.list(user=api_user)
    if status:
        orders = [o for o in orders if o.status == status]
    if inbox:
        orders = [o for o in orders if _inbox_keep(o, inbox)]
    if q:
        def _blob(o):
            return " ".join(filter(None, [
                o.document_number,
                o.vehicle.plate_number if o.vehicle else None,
                o.vehicle.conduction_number if o.vehicle else None,
                o.maintenance_type.name if o.maintenance_type else None,
                o.transaction_type.name if o.transaction_type else None,
                o.status,
            ])).lower()
        orders = [o for o in orders if q in _blob(o)]
    if cls:
        # already filtered via clause if list_filtered; here filter property
        orders = [
            o for o in orders
            if (getattr(o, "maintenance_class", None) or "").upper() == cls
        ]
    total = len(orders)
    start = (page - 1) * page_size
    slice_ = orders[start:start + page_size]
    return jsonify({
        "items": [_order_json(o) for o in slice_],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        # backward-compatible keys for older callers
        "count": total,
        "results": [_order_json(o) for o in slice_],
    })


@bp.route("/maintenance-orders/<int:oid>", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def get_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    from app.extensions import db
    from sqlalchemy.orm import joinedload, selectinload

    o = (db.session.query(MaintenanceOrder)
         .options(
             joinedload(MaintenanceOrder.vehicle),
             joinedload(MaintenanceOrder.maintenance_type),
             joinedload(MaintenanceOrder.transaction_type),
             joinedload(MaintenanceOrder.vendor),
             joinedload(MaintenanceOrder.driver),
             joinedload(MaintenanceOrder.origin_branch),
             joinedload(MaintenanceOrder.destination_branch),
             selectinload(MaintenanceOrder.checklist_items),
             selectinload(MaintenanceOrder.parts),
         )
         .filter_by(id=oid)
         .first())
    if o is None:
        return _not_found()
    data = _order_json(o, detail=True)
    # Letterhead for the print view -- from System Administration's
    # Company Profile, matching every one of Flask's print templates.
    from app.modules.api.company_letterhead import company_letterhead
    data["company"] = company_letterhead()
    data["editable"] = MaintenanceOrderService().editable_scope(o)
    data["approval_audit"] = _approval_audit(o)
    data["oath_body"] = _oath_body_for(o)
    _attach_approval_chain(data, o, api_user)
    return jsonify(data)


@bp.route("/maintenance-orders", methods=["POST"])
@api_auth_required("maintenanceorder.create")
def create_maintenance_order(api_user):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderCategoryError,
        InvalidOrderStateError)

    p = request.get_json(silent=True) or {}
    try:
        vehicle_id = int(p["vehicle_id"])
    except (KeyError, TypeError, ValueError):
        return _validation("vehicle_id is required.", "vehicle_id")
    sched = p.get("scheduled_date")
    if not sched:
        # Operational Assignment hides the date on the form but the
        # column is NOT NULL. Default to today (Flask uses the same
        # date as the VAM "Date" line).
        from datetime import date as _date
        sched = _date.today().isoformat()
    try:
        scheduled_date = date.fromisoformat(str(sched)[:10])
    except ValueError:
        return _validation("scheduled_date must be YYYY-MM-DD.", "scheduled_date")

    def _int(key):
        v = p.get(key)
        if v in (None, ""):
            return None
        return int(v)

    def _cost(key):
        v = p.get(key)
        if v in (None, ""):
            return None
        try:
            return Decimal(str(v))
        except (InvalidOperation, ValueError):
            return None

    try:
        order = MaintenanceOrderService().create(
            vehicle_id=vehicle_id,
            scheduled_date=scheduled_date,
            user=api_user,
            order_category=(p.get("order_category") or "MAINTENANCE"),
            maintenance_type_id=_int("maintenance_type_id"),
            transaction_type_id=_int("transaction_type_id"),
            scope_template_id=_int("scope_template_id"),
            pm_schedule_id=_int("pm_schedule_id"),
            description=p.get("description") or None,
            odometer_at_service=_int("odometer_at_service"),
            assigned_mechanic=p.get("assigned_mechanic") or None,
            vendor_id=_int("vendor_id"),
            estimated_cost=_cost("estimated_cost"),
            driver_id=_int("driver_id"),
            destination_branch_id=_int("destination_branch_id"),
            disposal_value=_cost("disposal_value"),
            disposal_recipient=p.get("disposal_recipient") or None,
            assignment_classification=p.get("assignment_classification") or None,
        )
    except InvalidOrderCategoryError as e:
        return _validation(str(e), "order_category")
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    except Exception as e:
        return _validation(str(e))

    return jsonify(_order_json(order, detail=True)), 201


@bp.route("/maintenance-orders/<int:oid>", methods=["PUT", "PATCH"])
@api_auth_required("maintenanceorder.update")
def update_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in (
        "scheduled_date", "odometer_at_service", "estimated_cost",
        "maintenance_type_id", "transaction_type_id",
        "assignment_classification", "assigned_mechanic", "description",
        "vendor_id",
    ):
        if k in p:
            fields[k] = p[k]
    try:
        order = MaintenanceOrderService().update(oid, user=api_user, **fields)
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    if order is None:
        return _not_found()
    return jsonify(_order_json(order, detail=True))


@bp.route("/maintenance-orders/<int:oid>/submit", methods=["POST"])
# Flask gates submit on maintenanceorder.update, not a
# ".submit" action -- no such action is ever registered
# (routes.py registers view/create/update/delete/print only),
# so the invented code could not be granted by ANY seed and
# the endpoint was permanently unusable for every user.
@api_auth_required("maintenanceorder.update")
def submit_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    try:
        MaintenanceOrderService().submit(oid, api_user)
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/start-work", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def start_work_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    try:
        MaintenanceOrderService().start_work(oid)
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/complete", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def complete_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, IncompleteChecklistError,
        InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    completed_date = p.get("completed_date") or date.today().isoformat()
    try:
        cd = date.fromisoformat(str(completed_date)[:10])
    except ValueError:
        return _validation("completed_date must be YYYY-MM-DD.", "completed_date")
    actual_cost = p.get("actual_cost")
    try:
        MaintenanceOrderService().complete(oid, actual_cost, cd)
    except IncompleteChecklistError as e:
        return _conflict(str(e))
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/cancel", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def cancel_maintenance_order(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    p = request.get_json(silent=True) or {}
    try:
        MaintenanceOrderService().cancel(oid, api_user, remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/checklist/<int:item_id>", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def toggle_checklist_item(api_user, oid, item_id):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    done = bool(p.get("done", True))
    try:
        MaintenanceOrderService().toggle_checklist_item(item_id, done, api_user)
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/checklist/mark-all", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def mark_all_checklist(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    done = bool(p.get("done", True))
    try:
        MaintenanceOrderService().mark_all_checklist_items(oid, done, api_user)
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/maintenance-orders/<int:oid>/parts", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def add_mo_part(api_user, oid):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    p = request.get_json(silent=True) or {}
    desc = (p.get("part_description") or "").strip()
    if not desc:
        return _validation("part_description is required.", "part_description")
    try:
        part = MaintenanceOrderService().add_part(
            oid,
            part_description=desc,
            quantity=p.get("quantity", 1),
            estimated_unit_cost=p.get("estimated_unit_cost", 0),
            part_number=p.get("part_number"),
            specification=p.get("specification"),
            uom=p.get("uom"),
            remarks=p.get("remarks"),
        )
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    except TypeError:
        # older signature without optional kwargs
        try:
            part = MaintenanceOrderService().add_part(
                oid,
                part_description=desc,
                quantity=p.get("quantity", 1),
                estimated_unit_cost=p.get("estimated_unit_cost", 0),
            )
        except Exception as e:
            return _conflict(str(e))
    return jsonify({"id": part.id if part else None, "ok": True}), 201


@bp.route("/maintenance-orders/<int:oid>/parts/<int:part_id>", methods=["DELETE"])
@api_auth_required("maintenanceorder.update")
def remove_mo_part(api_user, oid, part_id):
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService, InvalidOrderStateError)
    try:
        MaintenanceOrderService().remove_part(part_id)
    except InvalidOrderStateError as e:
        return _conflict(str(e))
    return jsonify({"ok": True})


@bp.route("/mo-transaction-types", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def list_mo_transaction_types(api_user):
    from app.modules.transactions.maintenance_order.service import (
        TransactionTypeService, PRINT_TEMPLATE_LABELS,
        resolve_print_template)
    cat = request.args.get("order_category")
    rows = TransactionTypeService().list(
        order_category=cat, include_inactive=False)
    return jsonify({
        "items": [
            {
                "id": t.id,
                "code": t.code,
                "name": t.name,
                "order_category": t.order_category,
                "group": t.group,
                "print_template": resolve_print_template(t),
                "print_label": PRINT_TEMPLATE_LABELS.get(
                    resolve_print_template(t), "Work Order"),
            }
            for t in rows
        ]
    })


@bp.route("/maintenance-orders/<int:oid>/cost-summary", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def maintenance_order_cost_summary(api_user, oid):
    """Actual cost for one order, grouped by the expense category used.

    The Cost Summary panel was drawn with three fixed rows -- Parts,
    Labor, Shop supplies -- and only the first two exist as stored
    fields on the invoice header:

        total_parts_cost = sum(line_amount where category == "PARTS")
        total_labor_cost = sum(line_amount where category == "LABOR")

    But EXPENSE_CATEGORY has nine values (PARTS, LABOR, TIRES, BATTERY,
    OIL, LUBRICANTS, EXTERNAL_SERVICES, TOWING, MISC). Seven of them are
    invisible in that header: a TOWING line lands in net_amount and
    total_invoice_amount, and under neither named row.

    So three fixed rows would present a breakdown that DOES NOT SUM TO
    THE TOTAL, on a document that authorises payment -- leaving money
    belonging to no row and nothing on screen to explain it.

    Grouping by the category actually used keeps every row real and
    makes the rows reconcile by construction. It also needs no schema
    change: expense_category is already NOT NULL on every line.

    A category with no lines is ABSENT, not zero. A zero row invites the
    reader to wonder what was meant to be there; absence says the
    invoice had none.

    Spans every invoice on the order. An MO can carry several -- parts
    from one supplier, labour from another -- and reading only the first
    would understate the spend being approved.

    Category rows are NET. VAT belongs to the invoice rather than to any
    category, and folding it into the rows would make them disagree with
    the invoice they came from, so it is reported separately.
    """
    from decimal import Decimal

    from app.modules.system_admin.services.lookup_service import LookupService
    from app.modules.transactions.maintenance_invoice.models import (
        MaintenanceInvoiceLine)
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    order = MaintenanceOrder.query.filter_by(id=oid).first()
    if order is None:
        return _not_found()

    invoice_ids = [inv.id for inv in order.invoices]
    lines = (MaintenanceInvoiceLine.query
             .filter(MaintenanceInvoiceLine.invoice_id.in_(invoice_ids))
             .all()) if invoice_ids else []

    # Labels come from the EXPENSE_CATEGORY lookup, not a map in code, so
    # a category renamed in Lookup Maintenance changes here too. Falls
    # back to the raw code rather than hiding a row whose lookup entry
    # was removed -- an unlabelled amount is still money.
    labels = {row.code: row.description
              for row in LookupService().get_by_type_with_fallback(
                  "EXPENSE_CATEGORY")}

    totals = {}
    for line in lines:
        code = line.expense_category
        totals[code] = totals.get(code, Decimal("0")) + (
            line.line_amount or Decimal("0"))

    by_category = [
        {"category": code,
         "label": labels.get(code, code),
         "amount": f"{amount:.2f}"}
        # Largest first: on a summary an approver skims, the number that
        # decides the answer should not be last.
        for code, amount in sorted(totals.items(), key=lambda kv: -kv[1])
    ]

    def _sum(attr):
        return sum((getattr(inv, attr) or Decimal("0"))
                   for inv in order.invoices) or Decimal("0")

    return jsonify({
        "by_category": by_category,
        "net_amount": f"{_sum('net_amount'):.2f}",
        "total_vat": f"{_sum('total_vat'):.2f}",
        "total_discount": f"{_sum('total_discount'):.2f}",
        "gross_amount": f"{_sum('gross_amount'):.2f}",
        "total_invoice_amount": f"{_sum('total_invoice_amount'):.2f}",
        # Carried alongside so the panel is readable BEFORE any invoice
        # exists -- which is exactly when approval happens.
        "estimated_cost": (f"{order.estimated_cost:.2f}"
                           if order.estimated_cost is not None else None),
        "actual_cost": (f"{order.actual_cost:.2f}"
                        if order.actual_cost is not None else None),
        "invoice_count": len(invoice_ids),
    })


def _oath_body_for(order):
    try:
        from app.modules.system_admin.services.print_template_service import (
            PrintTemplateService)
        svc = PrintTemplateService()
        tpl = svc.get("OATH_OF_UNDERTAKING")
        if tpl is None:
            return None
        return svc.render(tpl.body_html, order=order)
    except Exception:
        return None


def _attach_approval_chain(data, order, api_user):
    """Approval workflow for the detail screen's right panel.

    Same shape ATD already returns, built from the same
    ApprovalEngine.get_approval_chain(). Mirrored rather than
    reinvented: two shapes for one concept would mean two React
    components rendering the same workflow differently, and an approver
    seeing a different chain on ATD than on MO has no way to tell which
    one is right.

    A DRAFT order has no approval instance. That is the normal state of
    every order before submission, so the chain comes back EMPTY and the
    panel renders "not yet submitted" rather than failing.

    `can_act` drives whether the decision buttons appear. It asks the
    engine, not the payload -- offering Approve and Reject to someone
    who then gets a 403 tells them the system is broken, when in fact
    they were never the approver.
    """
    from app.core.approval.engine import ApprovalEngine

    inst = getattr(order, "approval_instance", None)
    engine = ApprovalEngine()
    chain = []
    if inst is not None:
        engine.repair_instance(inst)
        from app.extensions import db as _db
        try:
            _db.session.commit()
        except Exception:
            _db.session.rollback()
        for entry in engine.get_approval_chain(inst):
            acted_at = entry.get("acted_at")
            chain.append({
                "level_number": entry.get("level_number"),
                "approver_label": entry.get("approver_label"),
                # APPROVED | REJECTED | RETURNED | CURRENT | WAITING
                "status": entry.get("status"),
                "acted_by_name": entry.get("acted_by_name"),
                "acted_at": acted_at.isoformat() if acted_at else None,
                "remarks": entry.get("remarks"),
            })
        data["approval_instance_status"] = inst.status
        data["approval_current_level"] = inst.current_level
        data["can_act"] = bool(
            api_user and engine.is_eligible_approver(inst, api_user))
        data["is_final_level"] = bool(getattr(inst, "is_final_level", False))
    else:
        data["approval_instance_status"] = None
        data["approval_current_level"] = None
        data["can_act"] = False
    data["approval_chain"] = chain

    # Flask gates Submit on `not item.approval_instance`. Knowing the
    # instance EXISTS is different from knowing its status: a rejected
    # order has a status and still must not be submittable again.
    data["has_approval_instance"] = inst is not None

    # Flask shows Cancel only to the requester
    # (`item.requester.id == current_user.id`). Sent as a DECISION, not
    # as an id to compare: the client should not do identity arithmetic
    # to decide whether a destructive button appears, and shipping the
    # requester's user id to every reader discloses more than the
    # question needs.
    data["is_requester"] = bool(
        api_user is not None
        and getattr(order, "requested_by", None) == getattr(api_user, "id", None))
    return data


# ── Approval actions ────────────────────────────────────────────────────────
#
# Flask offers approve, reject and return on the MO detail screen. The
# API had none, so a React approver could READ the workflow and not act
# on it -- the screen showed a decision it could not carry out.
#
# Gated on `maintenanceorder.view`, exactly as the Jinja routes are,
# with ELIGIBILITY left to the ApprovalEngine. Holding view is not the
# same as being on the approval path, and re-checking eligibility here
# would be a second definition of who an approver is -- one that would
# drift from the engine the moment an approval path changed.
#
# Each returns the refreshed detail payload, including the recomputed
# approval chain, so the screen updates from the response rather than
# re-fetching and briefly showing a stale workflow.


def _approval_action(api_user, oid, method_name):
    """Shared body for approve / reject / return.

    Errors from the engine are 409, not 400: the request was
    well-formed and the DOCUMENT is in the wrong state, or the caller is
    not the approver. That is a conflict with reality, not malformed
    input, and telling the client "bad request" would send someone
    checking their payload for a fault that is not there.
    """
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    svc = MaintenanceOrderService()
    if MaintenanceOrder.query.filter_by(id=oid).first() is None:
        return _not_found()

    p = request.get_json(silent=True) or {}
    try:
        # remarks travels on every action. A rejection or return without
        # its reason leaves the requester knowing they must act and not
        # what to change.
        getattr(svc, method_name)(oid, user=api_user,
                                  remarks=p.get("remarks"))
    except Exception as e:
        return _conflict(str(e))

    o = MaintenanceOrder.query.filter_by(id=oid).first()
    data = _order_json(o, detail=True)
    data["editable"] = svc.editable_scope(o)
    _attach_approval_chain(data, o, api_user)
    return jsonify(data)


@bp.route("/maintenance-orders/<int:oid>/approve", methods=["POST"])
@api_auth_required("maintenanceorder.view")  # eligibility: ApprovalEngine
def approve_maintenance_order(api_user, oid):
    return _approval_action(api_user, oid, "approve")


@bp.route("/maintenance-orders/<int:oid>/reject", methods=["POST"])
@api_auth_required("maintenanceorder.view")
def reject_maintenance_order(api_user, oid):
    return _approval_action(api_user, oid, "reject")


@bp.route("/maintenance-orders/<int:oid>/return", methods=["POST"])
@api_auth_required("maintenanceorder.view")
def return_maintenance_order(api_user, oid):
    return _approval_action(api_user, oid, "return_document")


@bp.route("/maintenance-orders/<int:oid>/resubmit", methods=["POST"])
@api_auth_required("maintenanceorder.update")
def resubmit_maintenance_order(api_user, oid):
    """RETURNED orders go back for approval after correction. Without
    this a returned order is a dead end in React: the requester can see
    why it came back and has no way to send it on again."""
    return _approval_action(api_user, oid, "resubmit")


@bp.route("/maintenance-orders/<int:oid>/generate-pr", methods=["POST"])
@api_auth_required("purchaserequest.create")
def generate_purchase_request(api_user, oid):
    """Draft Purchase Request for an order that has parts but no PR.

    Automatic generation fires on the approval EVENT, so it only covers
    orders approved after that feature was installed. An order approved
    before then -- or one where generation failed and was logged rather
    than blocking the approval -- would otherwise be stuck with a parts
    list and no way to raise its PR without re-keying everything.

    Gated on purchaserequest.create, not maintenanceorder.view: raising
    a purchase request is procurement, not maintenance.

    Both of Flask's guards are kept, and both are 409 rather than 400 --
    the request is well-formed and the ORDER is in the wrong state:

      * already has a PR -- a second would duplicate the procurement and
        double-order the parts;
      * no parts -- a PR with no lines is a document nobody can act on,
        and generating one would look like success.

    Attributed to the ORDER'S REQUESTER, not whoever presses the button,
    so the PR names the person who actually stated the requirement.
    """
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    order = MaintenanceOrder.query.filter_by(id=oid).first()
    if order is None:
        return _not_found()
    if order.purchase_request_id:
        return _conflict("This order already has a Purchase Request.")
    if not order.parts:
        return _conflict("Add at least one part to procure before "
                         "generating a Purchase Request.")

    try:
        pr = MaintenanceOrderService().generate_purchase_request(
            oid, user=order.requester)
    except Exception as exc:
        from app.extensions import db
        db.session.rollback()
        return _conflict(str(exc))

    if pr is None:
        return _conflict("Nothing to generate for this order.")
    return jsonify({
        "id": pr.id,
        "document_number": getattr(pr, "document_number", None),
        "status": getattr(pr, "status", None),
    }), 201


@bp.route("/maintenance-orders/summary", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def maintenance_orders_summary(api_user):
    """Stat counts for the list screen's tile row.

    Built from a client mockup showing seven tiles (Total, For Approval,
    Submitted, Approved, Draft, Completed, Rejected). Two things in it
    are NOT built here, deliberately:

      * A Priority split. No such column exists on MaintenanceOrder, and
        Flask's own list route has no priority filter either
        (_txn_list_context). Inventing one would be exactly the kind of
        hardcoded, undecided business rule this project's own governing
        rule says not to add.
      * Treating "For Approval" and "Submitted" as different states.
        The real vocabulary (ApprovalInstance.status: PENDING /
        APPROVED / REJECTED / RETURNED / CANCELLED) has no state
        matching one of those two labels -- they are almost certainly
        the same PENDING state under two names in the mockup, or one
        means "pending at MY level" specifically. Guessing which would
        present an invented distinction as a real one.

    What IS built: the MO's own PHYSICAL status (draft / in_progress /
    completed / cancelled) counted separately from its APPROVAL status
    (pending_approval / approved / rejected / returned), because an
    order can be COMPLETED and have also been APPROVED earlier -- two
    different facts about the same order that a flat seven-tile list
    cannot represent without double-counting or losing one of them.

    An order with no approval instance at all (most orders, since
    submission is optional) counts toward NEITHER approval bucket --
    those describe orders that entered the workflow, not every order
    that exists.
    """
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    rows, _total = MaintenanceOrderService().list_filtered(
        user=api_user, page=1, per_page=100000)

    def physical(status):
        return sum(1 for o in rows if o.status == status)

    def approval(status):
        return sum(1 for o in rows
                   if o.approval_instance
                   and o.approval_instance.status == status)

    return jsonify({
        "total": len(rows),
        "draft": physical("DRAFT"),
        "in_progress": physical("IN_PROGRESS"),
        "completed": physical("COMPLETED"),
        "cancelled": physical("CANCELLED"),
        "pending_approval": approval("PENDING"),
        "approved": approval("APPROVED"),
        "rejected": approval("REJECTED"),
        "returned": approval("RETURNED"),
    })


@bp.route("/maintenance-orders/new-prefill", methods=["GET"])
@api_auth_required("maintenanceorder.create")
def maintenance_order_new_prefill(api_user):
    """Vehicle summary + PM package recommendation for the New
    Maintenance Order form.

    Matches Flask's own maintenanceorder_new route exactly, reusing the
    SAME service calls (PMScopeTemplateService.get_next_due_recommendation,
    list_applicable_for_vehicle) rather than re-deriving the
    recommendation logic here. Two implementations of "which PM package
    is next due" would eventually disagree, and the due-detection math
    (odometer AND date thresholds, whichever binds) is exactly the kind
    of logic that is easy to get subtly wrong a second time.

    This is what the Dashboard's "Due for Maintenance" list deep-links
    into: clicking a due vehicle was landing on a blank New Order form
    because nothing on the client read the ?vehicle_id= the link
    already carried, and nothing server-side pre-computed what Flask's
    own form shows automatically -- the vehicle's own current odometer,
    branch, and its recommended PM package with the reason it was
    chosen.
    """
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.maintenance_config.service import PMScopeTemplateService

    vehicle_id = request.args.get("vehicle_id", type=int)
    if not vehicle_id:
        return _validation("vehicle_id is required.", "vehicle_id")

    vehicle = VehicleService().get_visible(vehicle_id, api_user)
    if vehicle is None:
        return _not_found("Vehicle")

    maintenance_type_id = request.args.get("maintenance_type_id", type=int)

    templates = PMScopeTemplateService().list_applicable_for_vehicle(
        vehicle, maintenance_type_id=maintenance_type_id)
    rec = PMScopeTemplateService().get_next_due_recommendation(
        vehicle, maintenance_type_id=maintenance_type_id)
    due_template = PMScopeTemplateService().get_next_due_scope_template(
        vehicle, maintenance_type_id=maintenance_type_id)

    return jsonify({
        "vehicle": {
            "id": vehicle.id,
            "plate_number": vehicle.plate_number,
            "conduction_number": vehicle.conduction_number,
            "brand": vehicle.brand,
            "model": vehicle.model,
            "branch": vehicle.branch.name if vehicle.branch else None,
            "assigned_driver": (vehicle.assigned_driver.full_name
                               if vehicle.assigned_driver else None),
            "assigned_driver_position": (
                vehicle.assigned_driver.job_title
                if vehicle.assigned_driver else None),
            "current_odometer": vehicle.current_odometer,
        },
        "scope_templates": [
            {"id": t.id, "name": t.name,
             "maintenance_type_id": t.maintenance_type_id}
            for t in templates
        ],
        "pm_recommendation": {
            "status": rec.get("status"),
            "reason": rec.get("reason"),
            "due_by": rec.get("due_by"),
            "due_odometer": rec.get("due_odometer"),
            "due_date": (rec["due_date"].isoformat()
                        if rec.get("due_date") else None),
            # No package_name: checked against Flask's own template
            # afterward (should have been checked before writing this
            # the first time) -- maintenanceorder_form.html's
            # recommendation banner never displays a name or label for
            # the recommended package at all, only status/due_by/
            # due_odometer/due_date/reason/beyond_defined_cycle, all of
            # which are already here. PMSchedule has no naming column;
            # this field was invented from nothing, not merely
            # mis-spelled, and the fix is to drop it rather than guess
            # a second attribute name.
            "scope_template_id": due_template.id if due_template else None,
            "beyond_defined_cycle": rec.get("beyond_defined_cycle", False),
        },
    })


@bp.route("/maintenance-orders/scope-template-details", methods=["GET"])
@api_auth_required("maintenanceorder.create")
def maintenance_order_scope_template_details(api_user):
    """Powers the New Maintenance Order form's "Work Description
    Template" box and the collapsible "View Scope Details" table --
    reuses Flask's own get_pm_scope_template_details logic exactly
    (app/modules/api_search/routes.py), including token resolution
    against the vehicle (pm2/pm3/... become the vehicle's actual plate,
    driver, branch) so a fleet manager sees "65,000 km servicing of
    Toyota Vios, Plate no. NAO-907" rather than raw placeholders.

    {"found": false}, not a 404, for an unknown template -- matching
    Flask's own contract exactly, since the client's collapse panel
    just stays hidden either way.
    """
    from app.modules.maintenance_config.service import PMScopeTemplateService

    template_id = request.args.get("template_id", type=int)
    tmpl = (PMScopeTemplateService().get_by_id(template_id)
           if template_id else None)
    if tmpl is None:
        return jsonify({"found": False})

    items = sorted(tmpl.items, key=lambda i: i.sort_order)
    work_description = None
    if tmpl.pm_schedule and tmpl.pm_schedule.work_description_template:
        raw = tmpl.pm_schedule.work_description_template
        vehicle_id = request.args.get("vehicle_id", type=int)
        vehicle = None
        if vehicle_id:
            from app.modules.master_data.vehicle.models import Vehicle
            vehicle = Vehicle.query.filter_by(id=vehicle_id).first()
        if vehicle is not None:
            from app.core.reporting.token_resolver import resolve_pm_tokens
            work_description = resolve_pm_tokens(raw, vehicle=vehicle)
        else:
            work_description = raw

    return jsonify({
        "found": True,
        "name": tmpl.name,
        "work_description": work_description,
        "items": [{
            "sort_order": i.sort_order,
            "activity_code": i.activity_code,
            "activity_description": i.activity_description,
            "standard_labor_hours": (str(i.standard_labor_hours)
                                    if i.standard_labor_hours else None),
            "required_parts": i.required_parts,
        } for i in items],
    })


@bp.route("/maintenance-orders/<int:oid>/print-documents", methods=["GET"])
@api_auth_required("maintenanceorder.view")
def maintenance_order_print_documents(api_user, oid):
    """Which printed documents THIS order can actually produce.

    Flask has four distinct print outputs for a Maintenance Order --
    the generic work order, the Vehicle Assignment Memo, the Asset
    Transfer Report and the Asset Disposal Report -- and each of its
    routes gates on the DATA being present rather than on the
    transaction type code:

        no driver_id                 -> refuses the assignment memo
        no destination_branch_id     -> refuses the transfer report
        no disposal_reference_number -> refuses the disposal report

    That is the more robust rule and it is reused here rather than
    replaced with a code-to-template map. A DEP-ASSIGNMENT order whose
    driver was never set cannot produce a truthful memo, and a
    code-based map would offer the action anyway, only for the print
    route to refuse it. Flask's own comment says it plainly: the memo
    "is only true if it reflects what was actually approved".

    The Actions menu reads this list, so it names the actual document
    rather than a generic "Print" -- a fleet admin choosing between
    four possible outputs needs to know which one they are producing.
    """
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    o = MaintenanceOrder.query.filter_by(id=oid).first()
    if o is None:
        return _not_found("Maintenance Order")

    base = f"/maintenance-orders/{o.id}/print"
    documents = [{
        "code": "WORK_ORDER",
        "label": "Work Order",
        "url": base,
    }]

    # The transaction type's configured template, when it has one.
    # Held as DATA on the type (flask migration a7c419e35d02) so a
    # client mapping a NEW transaction type to a form in System
    # Administration does not need a developer. NULL means the generic
    # work order, which is already in the list above -- so an unmapped
    # type still prints something rather than nothing.
    _TEMPLATES = {
        "vehicle_assignment_memo":
            ("VEHICLE_ASSIGNMENT_MEMO", "Vehicle Assignment Memo", "vam"),
        "vehicle_reassignment_memo":
            ("VEHICLE_REASSIGNMENT_MEMO", "Vehicle Reassignment Memo", "vam"),
        "vehicle_relocation_memo":
            ("VEHICLE_RELOCATION_MEMO", "Vehicle Relocation Memo", "vam"),
        "maintenanceorder_print_transfer":
            ("ASSET_TRANSFER_REPORT", "Asset Transfer Report", "transfer"),
        "maintenanceorder_print_disposal":
            ("ASSET_DISPOSAL_REPORT", "Asset Disposal Report", "disposal"),
        "pm_work_order":
            ("PM_WORK_ORDER", "Preventive Maintenance Work Order", ""),
        "repair_work_order":
            ("REPAIR_WORK_ORDER", "Repair Work Order", ""),
    }
    configured = getattr(getattr(o, "transaction_type", None),
                         "print_template", None)
    entry = _TEMPLATES.get(configured or "")
    if entry:
        code, label, suffix = entry
        if code not in {d["code"] for d in documents}:
            documents.append({
                "code": code,
                "label": label,
                "url": f"{base}/{suffix}" if suffix else base,
            })

    known = {d["code"] for d in documents}
    if o.driver_id and "VEHICLE_ASSIGNMENT_MEMO" not in known:
        documents.append({
            "code": "VEHICLE_ASSIGNMENT_MEMO",
            "label": "Vehicle Assignment Memo",
            "url": f"{base}/vam",
        })
    if o.destination_branch_id and "ASSET_TRANSFER_REPORT" not in known:
        documents.append({
            "code": "ASSET_TRANSFER_REPORT",
            "label": "Asset Transfer Report",
            "url": f"{base}/transfer",
        })
    if o.disposal_reference_number and "ASSET_DISPOSAL_REPORT" not in known:
        documents.append({
            "code": "ASSET_DISPOSAL_REPORT",
            "label": "Asset Disposal Report",
            "url": f"{base}/disposal",
        })
    return jsonify({"documents": documents})
