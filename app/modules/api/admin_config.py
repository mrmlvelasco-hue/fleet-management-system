"""System Administration config APIs for React: company, lookups, parameters."""
from flask import jsonify, request

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="Record"):
    return jsonify({"error": "not_found", "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


# ── Company Profile ─────────────────────────────────────────────────────────

def _company_json(p):
    if p is None:
        return {
            "company_name": "",
            "address_line1": None,
            "address_line2": None,
            "city": None,
            "country": None,
            "phone": None,
            "email": None,
            "tin": None,
            "logo_url": None,
            "logo_filename": None,
            "logo_filename": None,
            "login_picture_url": None,
            "login_picture_filename": None,
        }
    return {
        "id": p.id,
        "company_name": p.company_name,
        "address_line1": p.address_line1,
        "address_line2": p.address_line2,
        "city": p.city,
        "country": p.country,
        "phone": p.phone,
        "email": p.email,
        "tin": p.tin,
        # Same shape as company_letterhead(): a URL when a logo is
        # configured, null otherwise, so the screen renders a preview or
        # an empty state without a second request.
        "logo_url": ("/api/v1/company/logo"
                     if getattr(p, "logo_attachment_id", None) else None),
        "logo_filename": p.logo_filename,
        "logo_filename": p.logo_filename,
        "login_picture_url": _login_picture_url(p),
        "login_picture_filename": getattr(p, "login_picture_filename", None),
    }


def _login_picture_url(company):
    """Public URL of the uploaded login picture, or None for the default.
    ?v=<attachment id> changes with every upload, so browsers fetch the
    new picture instead of showing a cached old one."""
    att_id = getattr(company, "login_picture_attachment_id", None) if company else None
    return f"/api/v1/branding/login-picture?v={att_id}" if att_id else None


@bp.route("/admin/company", methods=["GET"])
@api_auth_required("company.view")
def get_company(api_user):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    return jsonify(_company_json(CompanyProfileService().get()))


@bp.route("/admin/company", methods=["PUT", "PATCH"])
@api_auth_required("company.update")
def save_company(api_user):
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    p = request.get_json(silent=True) or {}
    name = (p.get("company_name") or "").strip()
    if not name:
        return _validation("company_name is required.", "company_name")
    fields = {"company_name": name}
    for k in ("address_line1", "address_line2", "city", "country",
              "phone", "email", "tin"):
        if k in p:
            v = p[k]
            fields[k] = (v.strip() if isinstance(v, str) else v) or None
    profile = CompanyProfileService().save(**fields)
    return jsonify(_company_json(profile))


# ── Lookups (admin) ─────────────────────────────────────────────────────────

def _lookup_json(r):
    return {
        "id": r.id,
        "lookup_type": r.lookup_type,
        "code": r.code,
        "description": r.description,
        "sort_order": r.sort_order,
        "is_active": bool(r.is_active),
    }


@bp.route("/admin/lookups", methods=["GET"])
@api_auth_required("lookup.view")
def list_lookups(api_user):
    from app.modules.system_admin.models import Lookup
    from app.extensions import db
    q = Lookup.query
    lt = (request.args.get("lookup_type") or "").strip().upper()
    if lt:
        q = q.filter_by(lookup_type=lt)
    include_inactive = (request.args.get("include_inactive") or "1") not in (
        "0", "false", "False")
    if not include_inactive:
        q = q.filter_by(is_active=True)
    rows = q.order_by(Lookup.lookup_type, Lookup.sort_order, Lookup.code).all()
    types = [
        r[0] for r in
        db.session.query(Lookup.lookup_type).distinct()
        .order_by(Lookup.lookup_type).all()
    ]
    return jsonify({
        "items": [_lookup_json(r) for r in rows],
        "types": types,
        "total": len(rows),
    })


@bp.route("/admin/lookups", methods=["POST"])
@api_auth_required("lookup.create")
def create_lookup(api_user):
    from app.modules.system_admin.services.lookup_service import LookupService
    p = request.get_json(silent=True) or {}
    lt = (p.get("lookup_type") or "").strip().upper()
    code = (p.get("code") or "").strip().upper()
    desc = (p.get("description") or "").strip()
    if not lt:
        return _validation("lookup_type is required.", "lookup_type")
    if not code:
        return _validation("code is required.", "code")
    if not desc:
        return _validation("description is required.", "description")
    try:
        sort_order = int(p.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    row = LookupService().create(
        lookup_type=lt, code=code, description=desc, sort_order=sort_order)
    return jsonify(_lookup_json(row)), 201


@bp.route("/admin/lookups/<int:lid>", methods=["PUT", "PATCH"])
@api_auth_required("lookup.update")
def update_lookup(api_user, lid):
    from app.modules.system_admin.services.lookup_service import LookupService
    p = request.get_json(silent=True) or {}
    fields = {}
    if "description" in p:
        fields["description"] = (p.get("description") or "").strip()
    if "sort_order" in p:
        try:
            fields["sort_order"] = int(p["sort_order"])
        except (TypeError, ValueError):
            return _validation("sort_order must be an integer.", "sort_order")
    if "is_active" in p:
        fields["is_active"] = bool(p["is_active"])
    row = LookupService().update(lid, **fields)
    if row is None:
        return _not_found("Lookup")
    return jsonify(_lookup_json(row))


@bp.route("/admin/lookups/<int:lid>/deactivate", methods=["POST"])
@api_auth_required("lookup.delete")
def deactivate_lookup(api_user, lid):
    from app.modules.system_admin.services.lookup_service import LookupService
    from app.modules.system_admin.models import Lookup
    from app.extensions import db
    row = db.session.get(Lookup, lid)
    if row is None:
        return _not_found("Lookup")
    LookupService().deactivate(lid)
    return jsonify({"ok": True})


# ── System Parameters ───────────────────────────────────────────────────────

@bp.route("/admin/parameters", methods=["GET"])
@api_auth_required("sysparam.view")
def list_parameters(api_user):
    from app.modules.system_admin.models import SystemParameter
    group = (request.args.get("group") or "").strip() or None
    q = SystemParameter.query.filter_by(is_active=True)
    if group:
        q = q.filter_by(group_name=group)
    rows = q.order_by(SystemParameter.group_name, SystemParameter.code).all()
    items = [{
        "id": p.id,
        "code": p.code,
        "name": getattr(p, "name", None) or p.code,
        "value": p.value,
        "data_type": p.data_type,
        "group_name": p.group_name,
        "description": getattr(p, "description", None),
    } for p in rows]
    groups = sorted({i["group_name"] for i in items if i["group_name"]})
    return jsonify({"items": items, "groups": groups, "total": len(items)})


@bp.route("/admin/parameters/<int:pid>", methods=["PUT", "PATCH"])
@api_auth_required("sysparam.update")
def update_parameter(api_user, pid):
    from app.modules.system_admin.models import SystemParameter
    from app.extensions import db
    row = db.session.get(SystemParameter, pid)
    if row is None or not row.is_active:
        return _not_found("Parameter")
    p = request.get_json(silent=True) or {}
    if "value" not in p:
        return _validation("value is required.", "value")
    row.value = str(p["value"])
    db.session.commit()
    return jsonify({
        "id": row.id,
        "code": row.code,
        "value": row.value,
        "data_type": row.data_type,
        "group_name": row.group_name,
    })


@bp.route("/admin/company/logo", methods=["POST"])
@api_auth_required("company.update")
def admin_company_logo_upload(api_user):
    """Upload the letterhead logo.

    Through AttachmentService, which already permits jpg/jpeg/png/gif in
    its default allow-list -- so nothing has to be widened and no new
    exposure is created. (The APK could not reuse it for exactly that
    reason: `apk` would have had to be added, letting anyone with
    vehicle.update attach an executable to a vehicle.)
    """
    from io import BytesIO  # noqa: F401  (kept for symmetry with download)
    from app.core.attachments.attachment_service import (
        AttachmentError, AttachmentService)
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "validation_error",
                        "message": "Choose an image file."}), 400

    company = CompanyProfileService().get()
    if company is None:
        return jsonify({
            "error": "validation_error",
            "message": ("Set up the Company Profile before uploading a "
                        "logo."),
        }), 400

    try:
        att = AttachmentService().upload(file, "company_profiles",
                                         company.id, user=api_user)
    except AttachmentError as e:
        return jsonify({"error": "validation_error",
                        "message": str(e)}), 400

    company.logo_attachment_id = att.id
    company.logo_filename = att.original_filename or att.filename
    db.session.commit()
    return jsonify({"logo_url": "/api/v1/company/logo",
                    "logo_filename": company.logo_filename}), 201


@bp.route("/admin/company/logo", methods=["DELETE"])
@api_auth_required("company.update")
def admin_company_logo_clear(api_user):
    """Remove the logo, falling the letterhead back to text only."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    company = CompanyProfileService().get()
    if company is not None:
        company.logo_attachment_id = None
        company.logo_filename = None
        db.session.commit()
    return jsonify({"logo_url": None})


@bp.route("/company/logo", methods=["GET"])
@api_auth_required()
def company_logo(api_user):
    """The letterhead logo image.

    Authenticated but with NO permission code, matching how the rest of
    the letterhead is exposed. Anyone who can print a document must be
    able to render its header; requiring company.view would put a System
    Administration permission in the path of an ordinary trip ticket.
    """
    from io import BytesIO
    from flask import send_file
    from app.core.models.attachment import Attachment
    from app.core.attachments.attachment_service import AttachmentService
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    company = CompanyProfileService().get()
    if company is None or not company.logo_attachment_id:
        return jsonify({"error": "not_found",
                        "message": "No logo configured."}), 404

    att = db.session.get(Attachment, company.logo_attachment_id)
    if att is None or not att.is_active:
        return jsonify({"error": "not_found",
                        "message": "No logo configured."}), 404

    # Bytes through the service, never att.file_data -- that is the
    # storage boundary, and a standing test enforces it.
    data = AttachmentService().get_bytes(att) or b""
    return send_file(BytesIO(data),
                     mimetype=att.mime_type or "image/png",
                     download_name=att.original_filename or "logo.png")



# ── Login page picture ──────────────────────────────────────────────────
#: A picture, not any attachment type the general allow-list accepts.
_LOGIN_PICTURE_TYPES = (".jpg", ".jpeg", ".png")
_LOGIN_PICTURE_MAX_BYTES = 3 * 1024 * 1024


@bp.route("/admin/company/login-picture", methods=["POST"])
@api_auth_required("company.update")
def admin_login_picture_upload(api_user):
    """Upload the picture shown on the right of the login page.

    Stored through AttachmentService like the logo (same storage and
    checks), but narrower: JPG/PNG only, at most 3 MB -- it is shown to
    every visitor of the login page."""
    from app.core.attachments.attachment_service import (
        AttachmentError, AttachmentService)
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)

    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "validation_error",
                        "message": "Choose a picture to upload."}), 400
    if not file.filename.lower().endswith(_LOGIN_PICTURE_TYPES):
        return jsonify({"error": "validation_error",
                        "message": "The login picture must be a JPG or PNG image."}), 400
    data = file.read()
    if len(data) > _LOGIN_PICTURE_MAX_BYTES:
        return jsonify({"error": "validation_error",
                        "message": "The login picture must be 3 MB or smaller."}), 400
    file.stream.seek(0)

    company = CompanyProfileService().get()
    if company is None:
        return jsonify({"error": "validation_error",
                        "message": ("Set up the Company Profile before uploading "
                                    "a login picture.")}), 400
    try:
        att = AttachmentService().upload(file, "company_profiles",
                                         company.id, user=api_user)
    except AttachmentError as e:
        return jsonify({"error": "validation_error", "message": str(e)}), 400

    company.login_picture_attachment_id = att.id
    company.login_picture_filename = att.original_filename or att.filename
    db.session.commit()
    return jsonify({"login_picture_url": _login_picture_url(company),
                    "login_picture_filename": company.login_picture_filename}), 201


@bp.route("/admin/company/login-picture", methods=["DELETE"])
@api_auth_required("company.update")
def admin_login_picture_clear(api_user):
    """Restore the default login picture."""
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    company = CompanyProfileService().get()
    if company is not None:
        company.login_picture_attachment_id = None
        company.login_picture_filename = None
        db.session.commit()
    return jsonify({"login_picture_url": None})


def _active_login_picture():
    from app.core.models.attachment import Attachment
    from app.modules.system_admin.services.company_service import (
        CompanyProfileService)
    company = CompanyProfileService().get()
    att_id = getattr(company, "login_picture_attachment_id", None) if company else None
    if not att_id:
        return company, None
    att = db.session.get(Attachment, att_id)
    if att is None or not att.is_active:
        return company, None
    return company, att


@bp.route("/branding", methods=["GET"])
def public_branding():
    """PUBLIC (no sign-in): what the login page needs before anyone signs
    in. Only the login picture's URL -- nothing about any user or record."""
    company, att = _active_login_picture()
    return jsonify({"login_picture_url": _login_picture_url(company) if att else None})


@bp.route("/branding/login-picture", methods=["GET"])
def public_login_picture():
    """PUBLIC (no sign-in): the login picture itself -- and only that.
    Served only if the stored file really is an image."""
    from io import BytesIO
    from flask import send_file
    from app.core.attachments.attachment_service import AttachmentService
    _, att = _active_login_picture()
    if att is None or not (att.mime_type or "").startswith("image/"):
        return jsonify({"error": "not_found",
                        "message": "No login picture configured."}), 404
    data = AttachmentService().get_bytes(att) or b""
    resp = send_file(BytesIO(data), mimetype=att.mime_type,
                     download_name=att.original_filename or "login-picture")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    # The ?v= in the URL changes with every upload, so caching is safe.
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp
