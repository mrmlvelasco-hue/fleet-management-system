"""Document Type + Numbering Scheme admin blueprint (thin controllers)."""
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.modules.document_config.forms import (
    DocumentTypeForm, NumberingSchemeForm)
from app.modules.document_config.repository import (
    DocumentTypeRepository, NumberingSchemeRepository)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService,
    DuplicateDocumentTypeError, DuplicateSchemeError)

bp = Blueprint("document_config", __name__, url_prefix="/admin",
               template_folder="templates")

for _code, _desc in [
    ("doctype.view", "View document types"),
    ("doctype.create", "Create document types"),
    ("doctype.update", "Update document types"),
    ("doctype.delete", "Deactivate document types"),
    ("numbering.view", "View numbering schemes"),
    ("numbering.create", "Create numbering schemes"),
    ("numbering.update", "Update numbering schemes"),
    ("numbering.delete", "Deactivate numbering schemes"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


_DT_FIELDS = ("code", "name", "description", "requires_approval",
              "auto_numbering", "printable", "mobile_available",
              "attachment_allowed")
_NS_FIELDS = ("document_type_id", "prefix", "suffix", "include_year",
              "include_month", "digit_count", "separator", "reset_policy")


def _form_data(form, fields):
    return {f: getattr(form, f).data for f in fields}


def _populate_scheme_form(form):
    form.document_type_id.choices = [
        (d.id, f"{d.code} — {d.name}") for d in DocumentTypeRepository().list()]


# ---------- Document Types ----------

@bp.route("/document-types")
@login_required
@require_permission("doctype.view")
def doctypes_list():
    items = DocumentTypeRepository().list(include_inactive=True)
    return render_template("document_config/doctypes_list.html", items=items)


@bp.route("/document-types/new", methods=["GET", "POST"])
@login_required
@require_permission("doctype.create")
def doctypes_new():
    form = DocumentTypeForm()
    if form.validate_on_submit():
        try:
            DocumentTypeService().create(**_form_data(form, _DT_FIELDS))
            flash("Document type created.", "success")
            return redirect(url_for("document_config.doctypes_list"))
        except DuplicateDocumentTypeError as exc:
            flash(str(exc), "danger")
    return render_template("document_config/doctype_form.html", form=form,
                           title="New Document Type")


@bp.route("/document-types/<int:doctype_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("doctype.update")
def doctypes_edit(doctype_id):
    item = DocumentTypeRepository().get_by_id(doctype_id, include_inactive=True)
    if item is None:
        flash("Document type not found.", "warning")
        return redirect(url_for("document_config.doctypes_list"))
    form = DocumentTypeForm(obj=item)
    if form.validate_on_submit():
        DocumentTypeService().update(doctype_id, **_form_data(form, _DT_FIELDS))
        flash("Document type updated.", "success")
        return redirect(url_for("document_config.doctypes_list"))
    return render_template("document_config/doctype_form.html", form=form,
                           title=f"Edit Document Type — {item.code}")


@bp.route("/document-types/<int:doctype_id>/deactivate", methods=["POST"])
@login_required
@require_permission("doctype.delete")
def doctypes_deactivate(doctype_id):
    DocumentTypeService().deactivate(doctype_id)
    flash("Document type deactivated.", "info")
    return redirect(url_for("document_config.doctypes_list"))


# ---------- Numbering Schemes ----------

@bp.route("/numbering-schemes")
@login_required
@require_permission("numbering.view")
def schemes_list():
    items = NumberingSchemeRepository().list(include_inactive=True)
    return render_template("document_config/schemes_list.html", items=items)


@bp.route("/numbering-schemes/new", methods=["GET", "POST"])
@login_required
@require_permission("numbering.create")
def schemes_new():
    form = NumberingSchemeForm()
    _populate_scheme_form(form)
    if form.validate_on_submit():
        try:
            NumberingSchemeService().create(**_form_data(form, _NS_FIELDS))
            flash("Numbering scheme created.", "success")
            return redirect(url_for("document_config.schemes_list"))
        except DuplicateSchemeError as exc:
            flash(str(exc), "danger")
    return render_template("document_config/scheme_form.html", form=form,
                           title="New Numbering Scheme")


@bp.route("/numbering-schemes/<int:scheme_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("numbering.update")
def schemes_edit(scheme_id):
    item = NumberingSchemeRepository().get_by_id(scheme_id,
                                                 include_inactive=True)
    if item is None:
        flash("Numbering scheme not found.", "warning")
        return redirect(url_for("document_config.schemes_list"))
    form = NumberingSchemeForm(obj=item)
    _populate_scheme_form(form)
    if form.validate_on_submit():
        NumberingSchemeService().update(scheme_id,
                                        **_form_data(form, _NS_FIELDS))
        flash("Numbering scheme updated.", "success")
        return redirect(url_for("document_config.schemes_list"))
    return render_template("document_config/scheme_form.html", form=form,
                           title="Edit Numbering Scheme")


@bp.route("/numbering-schemes/<int:scheme_id>/deactivate", methods=["POST"])
@login_required
@require_permission("numbering.delete")
def schemes_deactivate(scheme_id):
    NumberingSchemeService().deactivate(scheme_id)
    flash("Numbering scheme deactivated.", "info")
    return redirect(url_for("document_config.schemes_list"))
