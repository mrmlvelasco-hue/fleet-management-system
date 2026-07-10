"""Landing dashboard. KPI cards are placeholders until Phase 4."""
from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("main", __name__, template_folder="templates")


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
    return render_template("main/dashboard.html", cards=placeholder_cards)
