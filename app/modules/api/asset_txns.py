"""Tire and Battery Transaction APIs.

Phase 1 scope in the project's master prompt, with zero API coverage
before this.

What separates these from the other transaction modules: they have a
PHYSICAL EFFECT. Completing one changes the tire's or battery's own
status in Master Data, and DISPOSE also deactivates the record so a
disposed asset stops appearing as available stock. The services own
that logic entirely -- including WHEN it applies: immediately on create
when the Document Type needs no approval, and only on approval when it
does. This layer never touches asset status itself; doing so would put
a second, drifting copy of that rule outside the service.

Both modules are served from one file because they are the same shape,
and keeping them together makes a difference between them (tires can
be RETREADed; batteries cannot) visible rather than something one
copy silently inherits from the other.
"""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_COERCER = Coercer(
    ints={"tire_id": "Tire", "battery_id": "Battery",
          "vehicle_id": "Vehicle",
          "odometer_at_service": "Odometer at Service"},
    dates={"transaction_date": "Transaction Date"},
)


def _bad(message, field=None):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field or "_": message}}), 400


def _not_found(label):
    return jsonify({"error": "not_found",
                    "message": f"{label} not found."}), 404


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


def _iso(v):
    return v.isoformat() if v else None


def _vehicle_bits(txn):
    v = getattr(txn, "vehicle", None)
    return {
        "vehicle_id": txn.vehicle_id,
        "plate_number": v.plate_number if v else None,
        "conduction_number": v.conduction_number if v else None,
        "vehicle_label": (
            " ".join(x for x in [getattr(v, "brand", None),
                                 getattr(v, "model", None)] if x) or None
        ) if v else None,
    }


def _txn_json(txn, *, asset_attr, serial_key, detail=False,
              api_user=None):
    # odometer_at_service is read via getattr because BatteryTransaction
    # has no such column at all -- reporting None for a field the model
    # does not have is honest; inventing a key it could never populate
    # would not be.
    """One serialiser for both modules. `asset_attr` is "tire" or
    "battery"; the serial is surfaced under its own key because that
    is what identifies the item to a storeman -- an id alone would send
    them back to Master Data to look it up."""
    asset = getattr(txn, asset_attr, None)
    data = {
        "id": txn.id,
        "document_number": txn.document_number,
        f"{asset_attr}_id": getattr(txn, f"{asset_attr}_id"),
        serial_key: asset.serial_number if asset else None,
        f"{asset_attr}_brand": asset.brand if asset else None,
        "action": txn.action,
        "transaction_date": _iso(txn.transaction_date),
        "odometer_at_service": getattr(txn, "odometer_at_service", None),
        "remarks": txn.remarks,
        "status": txn.status,
        "requested_by": txn.requester.username if txn.requester else None,
    }
    data.update(_vehicle_bits(txn))
    if detail:
        from app.modules.api.company_letterhead import company_letterhead
        from app.modules.api.approval_eligibility import can_act_on

        data["company"] = company_letterhead()

        # Approval state.
        #
        # This payload carried only `status`, so after a successful
        # submit the screen had nothing to react to: it kept offering
        # Submit and Cancel, and the workflow panel kept saying "not yet
        # submitted". The submit itself worked -- the UI simply could
        # not see it.
        #
        # Same keys every other transaction module reports, so the
        # shared React panels behave identically here.
        # api_user is passed in rather than read from a global: the
        # serialiser is shared by list and detail across two modules,
        # and a hidden dependency on request state would make it work in
        # some of those paths and not others.
        inst = getattr(txn, "approval_instance", None)
        data["has_approval_instance"] = inst is not None
        data["approval_instance_status"] = inst.status if inst else None
        data["is_requester"] = bool(
            api_user and txn.requested_by == getattr(api_user, "id", None))
        data["can_act"] = can_act_on(inst, api_user, data.get("status"))
        data["approval_chain"] = _approval_chain(inst)
    return data


def _approval_chain(inst):
    """The levels, for the workflow panel.

    Built through ApprovalEngine rather than read off the instance --
    the ATD print endpoint hand-rolled its own version from attributes
    that do not exist, and printed an empty chain for months.
    """
    if inst is None:
        return []
    try:
        from app.core.approval.engine import ApprovalEngine
        return ApprovalEngine().get_approval_chain(inst) or []
    except Exception:
        # A workflow panel that cannot render must not take the whole
        # detail screen with it.
        return []


def _list(api_user, service_cls, **json_kw):
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", 25, type=int), 100)
    rows, pagination = service_cls().list_filtered(
        user=api_user, search=request.args.get("q") or None,
        status=request.args.get("status") or None,
        page=page, per_page=page_size)
    return jsonify({
        "items": [_txn_json(t, **json_kw) for t in rows],
        "total": pagination.total, "page": page, "page_size": page_size,
        "pages": pagination.pages,
    })


def _lifecycle(api_user, tid, method_name, model_cls, service_cls, label,
               **json_kw):
    if model_cls.query.filter_by(id=tid).first() is None:
        return _not_found(label)
    p = request.get_json(silent=True) or {}
    try:
        # submit() takes no `remarks`; approve/reject/cancel do.
        #
        # This dispatcher passed remarks to ALL of them, so every submit
        # raised TypeError -- caught below and returned as a 409 whose
        # message was a Python signature error. Tire and battery submit
        # was broken outright: the client pressed Submit, the request
        # failed, and the screen simply kept showing the pre-submit
        # state.
        #
        # Keyed off the method rather than swallowing the TypeError,
        # because "this argument does not apply here" is a fact about
        # the API, not an error to recover from.
        kwargs = {} if method_name == "submit" else {
            "remarks": p.get("remarks")}
        getattr(service_cls(), method_name)(tid, user=api_user, **kwargs)
    except Exception as exc:
        return _conflict(str(exc))
    fresh = model_cls.query.filter_by(id=tid).first()
    return jsonify(_txn_json(fresh, detail=True, api_user=api_user,
                             **json_kw))


# ── Tire transactions ───────────────────────────────────────────────────────

_TIRE_JSON = {"asset_attr": "tire", "serial_key": "tire_serial"}


@bp.route("/tire-transactions/form-options", methods=["GET"])
@api_auth_required("tire.view")
def tire_txn_form_options(api_user):
    """The actions the service will actually accept. Tires include
    RETREAD; batteries do not, and serving each module its own set
    keeps a real difference from being lost to a shared dropdown."""
    from app.modules.transactions.tire_txn.service import VALID_ACTIONS
    return jsonify({"actions": sorted(VALID_ACTIONS)})


@bp.route("/tire-transactions", methods=["GET"])
@api_auth_required("tire.view")
def list_tire_txns(api_user):
    from app.modules.transactions.tire_txn.service import (
        TireTransactionService)
    return _list(api_user, TireTransactionService, **_TIRE_JSON)


@bp.route("/tire-transactions", methods=["POST"])
@api_auth_required("tire.create")
def create_tire_txn(api_user):
    from app.modules.transactions.tire_txn.service import (
        InvalidTireActionError, TireTransactionService)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, [
            "tire_id", "vehicle_id", "odometer_at_service",
            "transaction_date"])
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if not fields.get("tire_id"):
        return _bad("Tire is required.", "tire_id")
    if not fields.get("transaction_date"):
        return _bad("Transaction Date is required.", "transaction_date")
    if not payload.get("action"):
        return _bad("Action is required.", "action")

    try:
        txn = TireTransactionService().create(
            tire_id=fields["tire_id"], action=payload["action"],
            transaction_date=fields["transaction_date"], user=api_user,
            vehicle_id=fields.get("vehicle_id"),
            odometer_at_service=fields.get("odometer_at_service"),
            remarks=payload.get("remarks") or None)
    except InvalidTireActionError as exc:
        return _bad(str(exc), "action")
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_txn_json(txn, detail=True, api_user=api_user,
                             **_TIRE_JSON)), 201


@bp.route("/tire-transactions/<int:tid>", methods=["GET"])
@api_auth_required("tire.view")
def tire_txn_detail(api_user, tid):
    from app.modules.transactions.tire_txn.models import TireTransaction

    txn = TireTransaction.query.filter_by(id=tid).first()
    if txn is None:
        return _not_found("Tire Transaction")
    return jsonify(_txn_json(txn, detail=True, api_user=api_user,
                             **_TIRE_JSON))


def _tire_lifecycle(api_user, tid, method_name):
    from app.modules.transactions.tire_txn.models import TireTransaction
    from app.modules.transactions.tire_txn.service import (
        TireTransactionService)
    return _lifecycle(api_user, tid, method_name, TireTransaction,
                      TireTransactionService, "Tire Transaction",
                      **_TIRE_JSON)


@bp.route("/tire-transactions/<int:tid>/submit", methods=["POST"])
@api_auth_required("tire.update")
def submit_tire_txn(api_user, tid):
    return _tire_lifecycle(api_user, tid, "submit")


@bp.route("/tire-transactions/<int:tid>/approve", methods=["POST"])
@api_auth_required("tire.view")
def approve_tire_txn(api_user, tid):
    """The service applies the physical effect here, not this layer --
    approving is the moment the tire's own status actually changes."""
    return _tire_lifecycle(api_user, tid, "approve")


@bp.route("/tire-transactions/<int:tid>/reject", methods=["POST"])
@api_auth_required("tire.view")
def reject_tire_txn(api_user, tid):
    return _tire_lifecycle(api_user, tid, "reject")


@bp.route("/tire-transactions/<int:tid>/cancel", methods=["POST"])
@api_auth_required("tire.update")
def cancel_tire_txn(api_user, tid):
    return _tire_lifecycle(api_user, tid, "cancel")


# ── Battery transactions ────────────────────────────────────────────────────

_BATT_JSON = {"asset_attr": "battery", "serial_key": "battery_serial"}


@bp.route("/battery-transactions/form-options", methods=["GET"])
@api_auth_required("battery.view")
def battery_txn_form_options(api_user):
    from app.modules.transactions.battery_txn.service import VALID_ACTIONS
    return jsonify({"actions": sorted(VALID_ACTIONS)})


@bp.route("/battery-transactions", methods=["GET"])
@api_auth_required("battery.view")
def list_battery_txns(api_user):
    from app.modules.transactions.battery_txn.service import (
        BatteryTransactionService)
    return _list(api_user, BatteryTransactionService, **_BATT_JSON)


@bp.route("/battery-transactions", methods=["POST"])
@api_auth_required("battery.create")
def create_battery_txn(api_user):
    from app.modules.transactions.battery_txn.service import (
        BatteryTransactionService, InvalidBatteryActionError)

    payload = request.get_json(silent=True) or {}
    try:
        fields = _COERCER.fields(payload, [
            "battery_id", "vehicle_id", "odometer_at_service",
            "transaction_date"])
    except FieldValueError as exc:
        return _bad(str(exc), exc.field)

    if not fields.get("battery_id"):
        return _bad("Battery is required.", "battery_id")
    if not fields.get("transaction_date"):
        return _bad("Transaction Date is required.", "transaction_date")
    if not payload.get("action"):
        return _bad("Action is required.", "action")

    try:
        # No odometer_at_service: BatteryTransaction genuinely has no
        # such column, unlike TireTransaction. A real difference
        # between two otherwise-identical modules -- accepting the
        # field here and silently dropping it would be worse than
        # refusing it, since the caller would believe it was recorded.
        txn = BatteryTransactionService().create(
            battery_id=fields["battery_id"], action=payload["action"],
            transaction_date=fields["transaction_date"], user=api_user,
            vehicle_id=fields.get("vehicle_id"),
            remarks=payload.get("remarks") or None)
    except InvalidBatteryActionError as exc:
        return _bad(str(exc), "action")
    except Exception as exc:
        return _conflict(str(exc))
    return jsonify(_txn_json(txn, detail=True, api_user=api_user,
                             **_BATT_JSON)), 201


@bp.route("/battery-transactions/<int:tid>", methods=["GET"])
@api_auth_required("battery.view")
def battery_txn_detail(api_user, tid):
    from app.modules.transactions.battery_txn.models import (
        BatteryTransaction)

    txn = BatteryTransaction.query.filter_by(id=tid).first()
    if txn is None:
        return _not_found("Battery Transaction")
    return jsonify(_txn_json(txn, detail=True, api_user=api_user,
                             **_BATT_JSON))


def _batt_lifecycle(api_user, tid, method_name):
    from app.modules.transactions.battery_txn.models import (
        BatteryTransaction)
    from app.modules.transactions.battery_txn.service import (
        BatteryTransactionService)
    return _lifecycle(api_user, tid, method_name, BatteryTransaction,
                      BatteryTransactionService, "Battery Transaction",
                      **_BATT_JSON)


@bp.route("/battery-transactions/<int:tid>/submit", methods=["POST"])
@api_auth_required("battery.update")
def submit_battery_txn(api_user, tid):
    return _batt_lifecycle(api_user, tid, "submit")


@bp.route("/battery-transactions/<int:tid>/approve", methods=["POST"])
@api_auth_required("battery.view")
def approve_battery_txn(api_user, tid):
    return _batt_lifecycle(api_user, tid, "approve")


@bp.route("/battery-transactions/<int:tid>/reject", methods=["POST"])
@api_auth_required("battery.view")
def reject_battery_txn(api_user, tid):
    return _batt_lifecycle(api_user, tid, "reject")


@bp.route("/battery-transactions/<int:tid>/cancel", methods=["POST"])
@api_auth_required("battery.update")
def cancel_battery_txn(api_user, tid):
    return _batt_lifecycle(api_user, tid, "cancel")
