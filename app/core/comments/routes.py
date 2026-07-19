"""Generic comment/discussion-thread routes — one POST endpoint reusable
by every module's detail page (reference_table + reference_id), same
pattern as the generic Attachment upload endpoint.
"""
from flask import Blueprint, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from app.extensions import db
from app.core.comments.comment_service import CommentService, EmptyCommentError
from app.core.attachments.attachment_service import AttachmentService, AttachmentError
from app.modules.user_management.models import User

bp = Blueprint("comments", __name__, url_prefix="/comments")

# Which base "view" permission gates commenting on each reference_table —
# a comment box only ever renders on a detail page the user could already
# view, so this mirrors that same page-level gate rather than duplicating
# the full org-scope visibility check here.
_REFERENCE_TABLE_PERMISSION = {
    "trip_tickets": "tripticket.view",
    "authority_to_drive": "atd.view",
    "vehicle_movements": "vehiclemovement.view",
    "maintenance_orders": "maintenanceorder.view",
    "purchase_requests": "purchaserequest.view",
    "vehicle_registrations": "vehicleregistration.view",
    "tire_transactions": "tiretxn.view",
    "battery_transactions": "batterytxn.view",
}


@bp.route("/<reference_table>/<int:reference_id>", methods=["POST"])
@login_required
def post_comment(reference_table, reference_id):
    perm = _REFERENCE_TABLE_PERMISSION.get(reference_table)
    if not perm or not current_user.has_permission(perm):
        abort(403)

    try:
        recipient_id = request.form.get("recipient_id")
        recipient = db.session.get(User, int(recipient_id)) if recipient_id else None
        file = request.files.get("attachment")
        comment = CommentService().create(
            reference_table=reference_table, reference_id=reference_id,
            author=current_user, body=request.form.get("body", ""),
            recipient=recipient, attachment_file=file)
        flash("Comment posted.", "success")
    except (EmptyCommentError, AttachmentError) as e:
        flash(str(e), "danger")

    next_url = request.form.get("next") or request.referrer or url_for("main.dashboard")
    return redirect(next_url)
