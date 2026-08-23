"""System Administration config APIs for React: company, lookups, parameters."""
from flask import jsonify, request

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
            "logo_filename": None,
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
        "logo_filename": p.logo_filename,
    }


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
