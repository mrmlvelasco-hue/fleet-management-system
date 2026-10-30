"""Vendor master endpoints for the React Master Data screens.

Jinja has list + create + edit + deactivate + contacts. Vendors are
scoped by assigned branches/BUs (empty = serves everyone).
"""
from datetime import date
from io import BytesIO

from flask import jsonify, request, send_file

from app.modules.api.auth import api_auth_required
from app.modules.api.coercion import Coercer, FieldValueError
from app.modules.api.routes import bp

_VENDOR_TYPES = ("GOODS", "SERVICES", "BOTH")
_WRITABLE = [
    "code", "name", "address", "city", "phone", "email",
    "tin", "contact_person", "vendor_type",
]
_COERCER = Coercer()


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _contact_json(c):
    return {
        "id": c.id,
        "contact_name": c.contact_name,
        "tel_number": c.tel_number,
        "cel_number": c.cel_number,
        "email": c.email,
        "position": c.position,
    }


def _serialise(v, *, detail=False):
    data = {
        "id": v.id,
        "code": v.code,
        "name": v.name,
        "city": v.city,
        "phone": v.phone,
        "email": v.email,
        "vendor_type": v.vendor_type,
        "is_active": bool(v.is_active),
        "contact_person": v.contact_person,
        # Batch 7: surfaced outside `detail` too, same as the rest of
        # this summary shape, so a list screen's row can show how a
        # vendor is scoped without a second round trip per row.
        "company_ids": [c.id for c in (v.companies or [])],
        "region_ids": [r.id for r in (v.regions or [])],
        "area_district_ids": [a.id for a in (v.area_districts or [])],
    }
    if detail:
        data.update({
            "address": v.address,
            "tin": v.tin,
            "branch_ids": [b.id for b in (v.branches or [])],
            "branches": [
                {"id": b.id, "code": getattr(b, "code", None), "name": b.name}
                for b in (v.branches or [])
            ],
            # Batch 7 (2026-10-31): Company/Region/Area-District
            # scoping, alongside Branch -- Michael's decision that
            # Vendor coverage must be configurable at all three.
            # "name" rather than "label" here, matching regions/
            # area_districts below -- all three are plain {id, name}
            # pairs, consistent for whichever of the three the React
            # coverage selector is rendering a chip for.
            "companies": [
                {"id": c.id, "name": c.short_name}
                for c in (v.companies or [])
            ],
            "regions": [
                {"id": r.id, "name": r.name} for r in (v.regions or [])
            ],
            "area_districts": [
                {"id": a.id, "name": a.name} for a in (v.area_districts or [])
            ],
            "contacts": [
                _contact_json(c) for c in (v.other_contacts or [])
                if getattr(c, "is_active", True)
            ],
        })
    return data


def _filtered(api_user):
    from app.modules.master_data.vendor.service import VendorService

    vendor_type = (request.args.get("vendor_type") or "").strip().upper()
    q = (request.args.get("q") or "").strip().lower()
    active = request.args.get("active")

    rows = VendorService().list(include_inactive=True, user=api_user)

    if vendor_type:
        if vendor_type not in _VENDOR_TYPES:
            return None, _bad(f"Unknown vendor_type '{vendor_type}'.")
        rows = [v for v in rows if (v.vendor_type or "").upper() == vendor_type]
    if active == "1":
        rows = [v for v in rows if v.is_active]
    elif active == "0":
        rows = [v for v in rows if not v.is_active]
    if q:
        rows = [v for v in rows if q in " ".join(filter(None, [
            v.code, v.name, v.city, v.phone, v.contact_person,
            v.vendor_type,
        ])).lower()]
    return rows, None


@bp.route("/vendors", methods=["GET"])
@api_auth_required("vendor.view")
def list_vendors(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 20))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    if page < 1 or page_size < 1:
        return _bad("page and page_size must be 1 or greater.")
    page_size = min(page_size, 200)
    total = len(rows)
    start = (page - 1) * page_size
    window = rows[start:start + page_size]
    return jsonify({
        "items": [_serialise(v) for v in window],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


@bp.route("/vendors/summary", methods=["GET"])
@api_auth_required("vendor.view")
def vendors_summary(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    return jsonify({
        "total": len(rows),
        "active": sum(1 for v in rows if v.is_active),
        "goods": sum(1 for v in rows if v.vendor_type == "GOODS"),
        "services": sum(1 for v in rows if v.vendor_type == "SERVICES"),
        "both": sum(1 for v in rows if v.vendor_type == "BOTH"),
    })


@bp.route("/vendors/export.xlsx", methods=["GET"])
@api_auth_required("vendor.view")
def vendors_export(api_user):
    rows, error = _filtered(api_user)
    if error is not None:
        return error
    try:
        from openpyxl import Workbook
    except ImportError:
        return _bad("Excel export is not available on this server.", 501)
    wb = Workbook()
    ws = wb.active
    ws.title = "Vendors"
    ws.append(["Code", "Name", "City", "Type", "Phone", "Email",
               "Contact", "TIN", "Active"])
    for v in rows:
        ws.append([
            v.code, v.name, v.city, v.vendor_type, v.phone, v.email,
            v.contact_person, v.tin, "Yes" if v.is_active else "No",
        ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf, as_attachment=True,
        download_name=f"vendors_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/vendors/import/template", methods=["GET"])
@api_auth_required("vendor.create")
def vendor_import_template_api(api_user):
    """Fill-in Excel template for the vendor bulk import (B24), with this
    install's own Branch codes on its Reference sheet. Same pattern and
    permission rule as /vehicles/import/template: importing is creating."""
    from io import BytesIO
    from flask import send_file
    from app.modules.master_data.vendor.import_service import build_template
    return send_file(
        BytesIO(build_template()), as_attachment=True,
        download_name="Vendor_Import_Template.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument"
                 ".spreadsheetml.sheet")


@bp.route("/vendors/import", methods=["POST"])
@api_auth_required("vendor.create")
def vendor_import_api(api_user):
    """Validate (and, only with commit=1, save) a filled-in template.

    Multipart: `file` (.xlsx), `commit` ("1" to save). A preview is the
    default, as for vehicles -- uploading the wrong file saves nothing.
    Existing codes are skipped and reported, never overwritten.

    Returns {dry_run, total_rows, created, skipped,
             errors: [{row, identifier, problems: [...]}]}.
    An unreadable file, or one missing required columns, is a 400.

    Registered BEFORE /vendors/<int:vendor_id>: "import" is not an int,
    so the order is not load-bearing today, but keeping the literal
    routes first means a future <vendor_code> route cannot capture it.
    """
    from app.modules.master_data.vendor.import_service import import_vendors
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "validation",
                        "message": "Choose a filled-in template file to "
                                   "upload."}), 400
    dry_run = request.form.get("commit") != "1"
    try:
        stats = import_vendors(uploaded.stream, dry_run=dry_run)
    except ValueError as exc:          # e.g. missing required columns
        return jsonify({"error": "validation", "message": str(exc)}), 400
    except Exception as exc:           # not an .xlsx, corrupt file, ...
        return jsonify({"error": "validation",
                        "message": f"Could not read the file: {exc}"}), 400
    return jsonify({"dry_run": dry_run, **stats})


@bp.route("/vendors/<int:vendor_id>", methods=["GET"])
@api_auth_required("vendor.view")
def vendor_detail(api_user, vendor_id):
    from app.modules.master_data.vendor.service import VendorService
    v = VendorService().get_visible(vendor_id, api_user)
    if v is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible to "
                                   "this account."}), 404
    return jsonify(_serialise(v, detail=True))


def _validation_response(exc):
    field = getattr(exc, "field", None) or "_"
    text = str(exc).lower()
    if "code" in text:
        field = "code"
    return jsonify({"error": "validation", "message": str(exc),
                    "fields": {field: str(exc)}}), 400


@bp.route("/vendors", methods=["POST"])
@api_auth_required("vendor.create")
def create_vendor(api_user):
    from app.modules.master_data.vendor.service import (
        VendorService, DuplicateCodeError)

    payload = request.get_json(silent=True) or {}
    try:
        fields = {k: payload.get(k) for k in _WRITABLE if k in payload}
        for req in ("code", "name"):
            if not (fields.get(req) or "").strip():
                return jsonify({"error": "validation",
                                "message": f"{req} is required.",
                                "fields": {req: "Required."}}), 400
        if fields.get("vendor_type") and fields["vendor_type"] not in _VENDOR_TYPES:
            return _bad(f"vendor_type must be one of {', '.join(_VENDOR_TYPES)}.")
        vendor = VendorService().create(
            code=fields.pop("code").strip(),
            name=fields.pop("name").strip(),
            vendor_type=fields.pop("vendor_type", None) or "GOODS",
            **{k: (v.strip() if isinstance(v, str) else v)
               for k, v in fields.items()})
        branch_ids = payload.get("branch_ids") or []
        if isinstance(branch_ids, list) and branch_ids:
            VendorService().assign_branches(
                vendor.id, [int(x) for x in branch_ids])
        # Batch 7 (2026-10-31): Company/Region/Area-District, alongside
        # branch_ids above -- same "only act when a non-empty list was
        # actually sent" rule, since create() has nothing to clear yet.
        company_ids = payload.get("company_ids") or []
        if isinstance(company_ids, list) and company_ids:
            VendorService().assign_companies(
                vendor.id, [int(x) for x in company_ids])
        region_ids = payload.get("region_ids") or []
        if isinstance(region_ids, list) and region_ids:
            VendorService().assign_regions(
                vendor.id, [int(x) for x in region_ids])
        area_district_ids = payload.get("area_district_ids") or []
        if isinstance(area_district_ids, list) and area_district_ids:
            VendorService().assign_area_districts(
                vendor.id, [int(x) for x in area_district_ids])
    except (DuplicateCodeError, FieldValueError, ValueError, TypeError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(vendor, detail=True)), 201


@bp.route("/vendors/<int:vendor_id>", methods=["PUT", "PATCH"])
@api_auth_required("vendor.update")
def update_vendor(api_user, vendor_id):
    from app.modules.master_data.vendor.service import VendorService

    svc = VendorService()
    if svc.get_visible(vendor_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible to "
                                   "this account."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        # code is immutable on edit (Jinja include_code=False)
        fields = {
            k: (v.strip() if isinstance(v, str) else v)
            for k, v in payload.items()
            if k in _WRITABLE and k != "code"
        }
        if fields.get("vendor_type") and fields["vendor_type"] not in _VENDOR_TYPES:
            return _bad(f"vendor_type must be one of {', '.join(_VENDOR_TYPES)}.")
        vendor = svc.update(vendor_id, **fields)
        if "branch_ids" in payload:
            branch_ids = payload.get("branch_ids") or []
            svc.assign_branches(vendor_id, [int(x) for x in branch_ids])
            vendor = svc.get(vendor_id)
        # Batch 7 (2026-10-31): Company/Region/Area-District, mirroring
        # branch_ids immediately above -- presence of the key (even as
        # an empty list) means "replace this axis", so a vendor's scope
        # can be cleared the same way branch_ids already can.
        if "company_ids" in payload:
            company_ids = payload.get("company_ids") or []
            svc.assign_companies(vendor_id, [int(x) for x in company_ids])
            vendor = svc.get(vendor_id)
        if "region_ids" in payload:
            region_ids = payload.get("region_ids") or []
            svc.assign_regions(vendor_id, [int(x) for x in region_ids])
            vendor = svc.get(vendor_id)
        if "area_district_ids" in payload:
            area_district_ids = payload.get("area_district_ids") or []
            svc.assign_area_districts(
                vendor_id, [int(x) for x in area_district_ids])
            vendor = svc.get(vendor_id)
    except (FieldValueError, ValueError, TypeError) as exc:
        return _validation_response(exc)
    return jsonify(_serialise(vendor, detail=True))


@bp.route("/vendors/<int:vendor_id>/deactivate", methods=["POST"])
@api_auth_required("vendor.delete")
def deactivate_vendor(api_user, vendor_id):
    from app.modules.master_data.vendor.service import VendorService
    svc = VendorService()
    if svc.get_visible(vendor_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible to "
                                   "this account."}), 404
    svc.deactivate(vendor_id)
    return jsonify({"ok": True})


@bp.route("/vendors/<int:vendor_id>/contacts", methods=["GET"])
@api_auth_required("vendor.view")
def list_vendor_contacts(api_user, vendor_id):
    from app.modules.master_data.vendor.service import (
        VendorService, VendorContactService)
    if VendorService().get_visible(vendor_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible."}), 404
    rows = VendorContactService().list_for_vendor(vendor_id)
    return jsonify({"items": [_contact_json(c) for c in rows]})


@bp.route("/vendors/<int:vendor_id>/contacts", methods=["POST"])
@api_auth_required("vendor.update")
def add_vendor_contact(api_user, vendor_id):
    from app.modules.master_data.vendor.service import (
        VendorService, VendorContactService)
    if VendorService().get_visible(vendor_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible."}), 404
    payload = request.get_json(silent=True) or {}
    name = (payload.get("contact_name") or "").strip()
    if not name:
        return jsonify({"error": "validation",
                        "message": "contact_name is required.",
                        "fields": {"contact_name": "Required."}}), 400
    contact = VendorContactService().create(
        vendor_id=vendor_id,
        contact_name=name,
        tel_number=(payload.get("tel_number") or None),
        cel_number=(payload.get("cel_number") or None),
        email=(payload.get("email") or None),
        position=(payload.get("position") or None),
    )
    return jsonify(_contact_json(contact)), 201


@bp.route("/vendors/<int:vendor_id>/contacts/<int:contact_id>", methods=["DELETE"])
@api_auth_required("vendor.update")
def delete_vendor_contact(api_user, vendor_id, contact_id):
    from app.modules.master_data.vendor.service import (
        VendorService, VendorContactService)
    if VendorService().get_visible(vendor_id, api_user) is None:
        return jsonify({"error": "not_found",
                        "message": "Vendor not found or not visible."}), 404
    VendorContactService().delete(contact_id)
    return jsonify({"ok": True})
