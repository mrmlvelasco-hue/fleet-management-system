"""Landing dashboard. KPI cards are placeholders until Phase 4; the "For
My Action" widget (F2/F3) is live — the generic Approval Task inbox."""
from datetime import datetime, timezone

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from app.core.approval.task_service import ApprovalTaskService
from app.core.approval.task_url_resolver import resolve_task_url

bp = Blueprint("main", __name__, template_folder="templates")


def _aging_label(created_at) -> str:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - created_at
    days = delta.days
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} waiting"
    hours = delta.seconds // 3600
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} waiting"
    return "Just now"


@bp.route("/")
@login_required
def dashboard():
    placeholder_cards = [
        {"title": "Fleet", "icon": "bi-truck", "value": "—"},
        {"title": "Maintenance", "icon": "bi-wrench", "value": "—"},
        {"title": "Approvals", "icon": "bi-check2-square", "value": "—"},
        {"title": "Registrations", "icon": "bi-card-checklist", "value": "—"},
        {"title": "Tires", "icon": "bi-circle", "value": "—"},
        {"title": "Batteries", "icon": "bi-battery-half", "value": "—"},
    ]

    my_tasks = ApprovalTaskService().list_for_user(current_user)
    for_my_action = [{
        "document_number": t.document_number or "(no number)",
        "document_type": t.document_type.name if t.document_type else "",
        "requester": t.requester.full_name if t.requester else "Unknown",
        "created_at": t.created_at,
        "aging": _aging_label(t.created_at),
        "level_number": t.level_number,
        "url": resolve_task_url(t),
    } for t in my_tasks]

    return render_template("main/dashboard.html", cards=placeholder_cards,
                           for_my_action=for_my_action)
