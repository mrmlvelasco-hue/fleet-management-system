"""Approval Path + Matrix admin blueprint (thin controllers).

Path levels arrive as parallel form arrays: approver_type[], role_id[],
user_id[] — assembled here into level dicts and validated by the service.
"""
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request)
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.approval_config.forms import (
    ApprovalPathForm, ApprovalMatrixForm)
from app.modules.approval_config.repository import (
    ApprovalPathRepository, ApprovalMatrixRepository)
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService,
    InvalidPathError, MatrixOverlapError)
from app.modules.document_config.repository import DocumentTypeRepository
from app.modules.user_management.repository import (
    RoleRepository, UserRepository)

bp = Blueprint("approval_config", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("approvalpath.view", "View approval paths"),
    ("approvalpath.create", "Create approval paths"),
    ("approvalpath.update", "Update approval paths"),
    ("approvalpath.delete", "Deactivate approval paths"),
    ("approvalmatrix.view", "View approval matrices"),
    ("approvalmatrix.create", "Create approval matrices"),
    ("approvalmatrix.update", "Update approval matrices"),
    ("approvalmatrix.delete", "Deactivate approval matrices"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


def _parse_levels() -> list[dict]:
    types = request.form.getlist("level_approver_type")
    role_ids = request.form.getlist("level_role_id")
    user_ids = request.form.getlist("level_user_id")
    levels = []
    for i, approver_type in enumerate(types):
        role_id = int(role_ids[i]) if i < len(role_ids) and role_ids[i] else None
        user_id = int(user_ids[i]) if i < len(user_ids) and user_ids[i] else None
        levels.append({
            "level_number": i + 1,
            "approver_type": approver_type,
            "role_id": role_id if approver_type == "ROLE" else None,
            "user_id": user_id if approver_type == "USER" else None,
        })
    return levels


def _selector_data():
    return {
        "roles": RoleRepository().list(),
        "users": UserRepository().list(),
    }


def _populate_matrix_form(form: ApprovalMatrixForm) -> None:
    form.document_type_id.choices = [
        (d.id, f"{d.code} — {d.name}") for d in DocumentTypeRepository().list()]
    form.approval_path_id.choices = [
        (p.id, p.name) for p in ApprovalPathRepository().list()]


# ---------- Approval Paths ----------

@bp.route("/approval-paths")
@login_required
@require_permission("approvalpath.view")
def paths_list():
    items = ApprovalPathRepository().list(include_inactive=True)
    return render_template("approval_config/paths_list.html", items=items)


@bp.route("/approval-paths/new", methods=["GET", "POST"])
@login_required
@require_permission("approvalpath.create")
def paths_new():
    form = ApprovalPathForm()
    if form.validate_on_submit():
        try:
            ApprovalPathService().create(
                name=form.name.data, description=form.description.data,
                levels=_parse_levels())
            flash("Approval path created.", "success")
            return redirect(url_for("approval_config.paths_list"))
        except InvalidPathError as exc:
            flash(str(exc), "danger")
    return render_template("approval_config/path_form.html", form=form,
                           title="New Approval Path", path=None,
                           **_selector_data())


@bp.route("/approval-paths/<int:path_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("approvalpath.update")
def paths_edit(path_id):
    path = ApprovalPathRepository().get_by_id(path_id, include_inactive=True)
    if path is None:
        flash("Approval path not found.", "warning")
        return redirect(url_for("approval_config.paths_list"))
    form = ApprovalPathForm(obj=path)
    if form.validate_on_submit():
        try:
            ApprovalPathService().update(
                path_id, name=form.name.data,
                description=form.description.data, levels=_parse_levels())
            flash("Approval path updated.", "success")
            return redirect(url_for("approval_config.paths_list"))
        except InvalidPathError as exc:
            flash(str(exc), "danger")
    return render_template("approval_config/path_form.html", form=form,
                           title=f"Edit Approval Path — {path.name}",
                           path=path, **_selector_data())


@bp.route("/approval-paths/<int:path_id>/deactivate", methods=["POST"])
@login_required
@require_permission("approvalpath.delete")
def paths_deactivate(path_id):
    ApprovalPathService().deactivate(path_id)
    flash("Approval path deactivated.", "info")
    return redirect(url_for("approval_config.paths_list"))


# ---------- Approval Matrix ----------

@bp.route("/approval-matrix")
@login_required
@require_permission("approvalmatrix.view")
def matrix_list():
    items = ApprovalMatrixRepository().list(include_inactive=True)
    return render_template("approval_config/matrix_list.html", items=items)


@bp.route("/approval-matrix/new", methods=["GET", "POST"])
@login_required
@require_permission("approvalmatrix.create")
def matrix_new():
    form = ApprovalMatrixForm()
    _populate_matrix_form(form)
    if form.validate_on_submit():
        try:
            ApprovalMatrixService().create(
                document_type_id=form.document_type_id.data,
                approval_path_id=form.approval_path_id.data,
                min_amount=form.min_amount.data,
                max_amount=form.max_amount.data,
                effective_from=form.effective_from.data,
                effective_to=form.effective_to.data)
            flash("Approval matrix entry created.", "success")
            return redirect(url_for("approval_config.matrix_list"))
        except MatrixOverlapError as exc:
            flash(str(exc), "danger")
    return render_template("approval_config/matrix_form.html", form=form,
                           title="New Approval Matrix Entry")


@bp.route("/approval-matrix/<int:matrix_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("approvalmatrix.update")
def matrix_edit(matrix_id):
    item = ApprovalMatrixRepository().get_by_id(matrix_id,
                                                include_inactive=True)
    if item is None:
        flash("Matrix entry not found.", "warning")
        return redirect(url_for("approval_config.matrix_list"))
    form = ApprovalMatrixForm(obj=item)
    _populate_matrix_form(form)
    if form.validate_on_submit():
        try:
            ApprovalMatrixService().update(
                matrix_id,
                document_type_id=form.document_type_id.data,
                approval_path_id=form.approval_path_id.data,
                min_amount=form.min_amount.data,
                max_amount=form.max_amount.data,
                effective_from=form.effective_from.data,
                effective_to=form.effective_to.data)
            flash("Approval matrix entry updated.", "success")
            return redirect(url_for("approval_config.matrix_list"))
        except MatrixOverlapError as exc:
            flash(str(exc), "danger")
    return render_template("approval_config/matrix_form.html", form=form,
                           title="Edit Approval Matrix Entry")


@bp.route("/approval-matrix/<int:matrix_id>/deactivate", methods=["POST"])
@login_required
@require_permission("approvalmatrix.delete")
def matrix_deactivate(matrix_id):
    ApprovalMatrixService().deactivate(matrix_id)
    flash("Approval matrix entry deactivated.", "info")
    return redirect(url_for("approval_config.matrix_list"))
