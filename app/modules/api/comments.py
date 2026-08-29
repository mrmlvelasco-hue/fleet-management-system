"""JSON comment thread used by React transaction detail screens."""
from flask import jsonify, request

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.core.comments.comment_service import CommentService, EmptyCommentError
from app.core.comments.models import DocumentComment
from app.modules.user_management.models import User

_TABLE_PERM = {
    "trip_tickets": "tripticket.view",
    "authority_to_drives": "atd.view",
    "vehicle_movements": "vehiclemovement.view",
    "vehicle_checklists": "checklist.view",
    "maintenance_orders": "maintenanceorder.view",
    "purchase_requests": "purchaserequest.view",
    "vehicle_registrations": "vehicleregistration.view",
    "tire_transactions": "tire.view",
    "battery_transactions": "battery.view",
    "fuel_transactions": "fuel.view",
}


def _iso(v):
    return v.isoformat() if v else None


def _name(user):
    if user is None:
        return None
    full = getattr(user, "full_name", None)
    if full:
        return full
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    joined = " ".join(p for p in parts if p)
    return joined or user.username


def _comment_json(c):
    atts = []
    try:
        from app.core.attachments.models import Attachment
        rows = Attachment.query.filter_by(
            reference_table="document_comments", reference_id=c.id).all()
        atts = [{"id": a.id, "filename": a.original_filename} for a in rows]
    except Exception:
        atts = []
    roles = []
    try:
        roles = [r.name for r in (c.author.roles or [])] if c.author else []
    except Exception:
        roles = []
    return {
        "id": c.id,
        "body": c.body,
        "author_id": c.author_id,
        "author_name": _name(c.author),
        "author_role": roles[0] if roles else None,
        "recipient_id": c.recipient_id,
        "recipient_name": _name(c.recipient),
        "created_at": _iso(c.created_at),
        "attachments": atts,
    }


def _gate(table, api_user):
    perm = _TABLE_PERM.get(table)
    if not perm:
        return False
    return bool(api_user.has_permission(perm))


@bp.route("/comments/<reference_table>/<int:reference_id>", methods=["GET"])
@api_auth_required()
def list_comments(api_user, reference_table, reference_id):
    if not _gate(reference_table, api_user):
        return jsonify({"error": "forbidden", "message": "Not allowed."}), 403
    rows = CommentService().list_for(reference_table, reference_id)
    return jsonify({"items": [_comment_json(c) for c in rows]})


@bp.route("/comments/<reference_table>/<int:reference_id>", methods=["POST"])
@api_auth_required()
def post_comment_api(api_user, reference_table, reference_id):
    if not _gate(reference_table, api_user):
        return jsonify({"error": "forbidden", "message": "Not allowed."}), 403
    payload = request.get_json(silent=True) if request.is_json else None
    if payload:
        body = payload.get("body") or payload.get("comment") or ""
        recipient_id = payload.get("recipient_id")
        attachment = None
    else:
        body = request.form.get("body") or request.form.get("comment") or ""
        recipient_id = request.form.get("recipient_id")
        attachment = request.files.get("attachment")
    recipient = None
    if recipient_id:
        try:
            recipient = db.session.get(User, int(recipient_id))
        except (TypeError, ValueError):
            recipient = None
    try:
        comment = CommentService().create(
            reference_table=reference_table,
            reference_id=reference_id,
            author=api_user,
            body=body,
            recipient=recipient,
            attachment_file=attachment,
        )
    except EmptyCommentError as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    return jsonify(_comment_json(comment)), 201
