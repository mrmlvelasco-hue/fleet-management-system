"""Users, Roles, Numbering schemes, Approval paths/matrix for React admin."""
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


def _conflict(message):
    return jsonify({"error": "conflict", "message": message}), 409


# ── Users ───────────────────────────────────────────────────────────────────

def _user_json(u, *, detail=False):
    data = {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "full_name": getattr(u, "full_name", None) or u.username,
        "employee_id": u.employee_id,
        "branch_id": u.branch_id,
        "branch": u.branch.name if getattr(u, "branch", None) else None,
        "department_id": u.department_id,
        "department": (
            u.department.name if getattr(u, "department", None) else None),
        "is_active": bool(u.is_active),
        "is_superuser": bool(getattr(u, "is_superuser", False)),
        "failed_login_attempts": u.failed_login_attempts or 0,
        "last_login_at": (
            u.last_login_at.isoformat() if u.last_login_at else None),
        "roles": [{"id": r.id, "name": r.name} for r in (u.roles or [])],
    }
    if detail:
        data["role_ids"] = [r.id for r in (u.roles or [])]
    return data


@bp.route("/admin/users", methods=["GET"])
@api_auth_required("user.view")
def list_users(api_user):
    from app.modules.user_management.models import User
    from sqlalchemy.orm import joinedload
    q = (request.args.get("q") or "").strip().lower()
    include_inactive = (request.args.get("include_inactive") or "1") not in (
        "0", "false", "False")
    query = User.query.options(
        joinedload(User.roles),
        joinedload(User.branch),
        joinedload(User.department),
    )
    if not include_inactive:
        query = query.filter_by(is_active=True)
    rows = query.order_by(User.username).all()
    items = []
    for u in rows:
        if q:
            hay = " ".join(filter(None, [
                u.username, u.email, u.first_name, u.last_name, u.employee_id,
            ])).lower()
            if q not in hay:
                continue
        items.append(_user_json(u))
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(int(request.args.get("page_size", 25)), 100))
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")
    total = len(items)
    start = (page - 1) * page_size
    return jsonify({
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    })


@bp.route("/admin/users/<int:uid>", methods=["GET"])
@api_auth_required("user.view")
def get_user(api_user, uid):
    from app.modules.user_management.models import User
    from sqlalchemy.orm import joinedload
    u = (User.query.options(
        joinedload(User.roles), joinedload(User.branch),
        joinedload(User.department)).filter_by(id=uid).first())
    if u is None:
        return _not_found("User")
    return jsonify(_user_json(u, detail=True))


@bp.route("/admin/users", methods=["POST"])
@api_auth_required("user.create")
def create_user(api_user):
    from app.modules.user_management.service import UserService
    p = request.get_json(silent=True) or {}
    username = (p.get("username") or "").strip()
    email = (p.get("email") or "").strip()
    password = p.get("password") or ""
    if not username:
        return _validation("username is required.", "username")
    if not email:
        return _validation("email is required.", "email")
    if not password:
        return _validation("password is required.", "password")
    try:
        u = UserService().create_user(
            username=username,
            email=email,
            password=password,
            first_name=(p.get("first_name") or "").strip() or None,
            last_name=(p.get("last_name") or "").strip() or None,
            employee_id=(p.get("employee_id") or "").strip() or None,
            branch_id=p.get("branch_id") or None,
            department_id=p.get("department_id") or None,
            role_ids=p.get("role_ids") or [],
        )
    except Exception as e:
        return _conflict(str(e))
    return jsonify(_user_json(u, detail=True)), 201


@bp.route("/admin/users/<int:uid>", methods=["PUT", "PATCH"])
@api_auth_required("user.update")
def update_user(api_user, uid):
    from app.modules.user_management.service import UserService
    p = request.get_json(silent=True) or {}
    kwargs = {}
    for k in ("email", "first_name", "last_name", "employee_id"):
        if k in p:
            kwargs[k] = (p[k] or "").strip() or None
    if "branch_id" in p:
        kwargs["branch_id"] = p["branch_id"] or None
    if "department_id" in p:
        kwargs["department_id"] = p["department_id"] or None
    if "password" in p and p["password"]:
        kwargs["password"] = p["password"]
    if "role_ids" in p:
        kwargs["role_ids"] = p["role_ids"] or []
    try:
        u = UserService().update_user(uid, **kwargs)
    except Exception as e:
        return _conflict(str(e))
    if u is None:
        return _not_found("User")
    return jsonify(_user_json(u, detail=True))


@bp.route("/admin/users/<int:uid>/deactivate", methods=["POST"])
@api_auth_required("user.delete")
def deactivate_user(api_user, uid):
    from app.modules.user_management.service import UserService
    UserService().deactivate_user(uid)
    return jsonify({"ok": True})


@bp.route("/admin/users/<int:uid>/unlock", methods=["POST"])
@api_auth_required("user.update")
def unlock_user(api_user, uid):
    from app.modules.user_management.service import UserService
    u = UserService().unlock_user(uid)
    if u is None:
        return _not_found("User")
    return jsonify(_user_json(u))


# ── Roles & Permissions ─────────────────────────────────────────────────────

def _role_json(r, *, detail=False):
    data = {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "is_system_role": bool(r.is_system_role),
        "is_active": bool(r.is_active),
        "permission_count": len(r.permissions or []),
    }
    if detail:
        data["permissions"] = [
            {"id": p.id, "code": p.code, "module": p.module,
             "action": p.action, "description": p.description}
            for p in (r.permissions or [])
        ]
        data["permission_ids"] = [p.id for p in (r.permissions or [])]
    return data


@bp.route("/admin/roles", methods=["GET"])
@api_auth_required("role.view")
def list_roles(api_user):
    from app.modules.user_management.models import Role
    from sqlalchemy.orm import selectinload
    rows = (Role.query.options(selectinload(Role.permissions))
            .order_by(Role.name).all())
    return jsonify({
        "items": [_role_json(r) for r in rows],
        "total": len(rows),
    })


@bp.route("/admin/roles/<int:rid>", methods=["GET"])
@api_auth_required("role.view")
def get_role(api_user, rid):
    from app.modules.user_management.models import Role
    from sqlalchemy.orm import selectinload
    r = (Role.query.options(selectinload(Role.permissions))
         .filter_by(id=rid).first())
    if r is None:
        return _not_found("Role")
    return jsonify(_role_json(r, detail=True))


@bp.route("/admin/roles", methods=["POST"])
@api_auth_required("role.create")
def create_role(api_user):
    from app.modules.user_management.service import RoleService
    p = request.get_json(silent=True) or {}
    name = (p.get("name") or "").strip()
    if not name:
        return _validation("name is required.", "name")
    try:
        r = RoleService().create_role(
            name=name,
            description=(p.get("description") or "").strip() or None,
            permission_ids=p.get("permission_ids") or [],
        )
    except Exception as e:
        return _conflict(str(e))
    return jsonify(_role_json(r, detail=True)), 201


@bp.route("/admin/roles/<int:rid>", methods=["PUT", "PATCH"])
@api_auth_required("role.update")
def update_role(api_user, rid):
    from app.modules.user_management.service import RoleService
    p = request.get_json(silent=True) or {}
    kwargs = {}
    if "name" in p:
        kwargs["name"] = (p.get("name") or "").strip()
    if "description" in p:
        kwargs["description"] = (p.get("description") or "").strip() or None
    if "permission_ids" in p:
        kwargs["permission_ids"] = p["permission_ids"] or []
    try:
        r = RoleService().update_role(rid, **kwargs)
    except Exception as e:
        return _conflict(str(e))
    if r is None:
        return _not_found("Role")
    return jsonify(_role_json(r, detail=True))


@bp.route("/admin/permissions", methods=["GET"])
@api_auth_required("role.view")
def list_permissions(api_user):
    from app.modules.user_management.models import Permission
    rows = Permission.query.order_by(
        Permission.module, Permission.code).all()
    items = [{
        "id": p.id,
        "code": p.code,
        "module": p.module,
        "action": p.action,
        "description": p.description,
    } for p in rows]
    modules = sorted({i["module"] for i in items if i["module"]})
    return jsonify({"items": items, "modules": modules, "total": len(items)})


# ── Numbering Schemes ───────────────────────────────────────────────────────

def _scheme_json(s):
    from app.modules.document_config.service import NumberingSchemeService
    preview = NumberingSchemeService.preview(s)
    dt = s.document_type
    return {
        "id": s.id,
        "document_type_id": s.document_type_id,
        "document_type_code": dt.code if dt else None,
        "document_type_name": dt.name if dt else None,
        "prefix": s.prefix,
        "suffix": s.suffix,
        "include_year": bool(s.include_year),
        "include_month": bool(s.include_month),
        "digit_count": s.digit_count,
        "separator": s.separator,
        "reset_policy": s.reset_policy,
        "is_active": bool(s.is_active),
        "preview": preview,
    }


@bp.route("/admin/numbering", methods=["GET"])
@api_auth_required("numbering.view")
def list_numbering(api_user):
    from app.modules.document_config.models import NumberingScheme
    from sqlalchemy.orm import joinedload
    rows = (NumberingScheme.query
            .options(joinedload(NumberingScheme.document_type))
            .order_by(NumberingScheme.id).all())
    return jsonify({
        "items": [_scheme_json(s) for s in rows],
        "total": len(rows),
    })


@bp.route("/admin/numbering/<int:sid>", methods=["PUT", "PATCH"])
@api_auth_required("numbering.update")
def update_numbering(api_user, sid):
    from app.modules.document_config.service import NumberingSchemeService
    from app.modules.document_config.models import NumberingScheme
    from sqlalchemy.orm import joinedload
    p = request.get_json(silent=True) or {}
    allowed = ("prefix", "suffix", "include_year", "include_month",
               "digit_count", "separator", "reset_policy")
    kwargs = {k: p[k] for k in allowed if k in p}
    if "digit_count" in kwargs:
        try:
            kwargs["digit_count"] = int(kwargs["digit_count"])
        except (TypeError, ValueError):
            return _validation("digit_count must be an integer.", "digit_count")
    try:
        NumberingSchemeService().update(sid, **kwargs)
    except Exception as e:
        return _conflict(str(e))
    s = (NumberingScheme.query.options(joinedload(NumberingScheme.document_type))
         .filter_by(id=sid).first())
    if s is None:
        return _not_found("Numbering scheme")
    return jsonify(_scheme_json(s))


# ── Approval Paths ──────────────────────────────────────────────────────────

def _path_json(path, *, detail=False):
    data = {
        "id": path.id,
        "name": path.name,
        "description": path.description,
        "is_active": bool(path.is_active),
        "level_count": len(path.levels or []),
    }
    if detail:
        levels = []
        for lv in (path.levels or []):
            levels.append({
                "id": lv.id,
                "level_number": lv.level_number,
                "approver_type": lv.approver_type,
                "role_id": lv.role_id,
                "role_name": lv.role.name if lv.role else None,
                "user_id": lv.user_id,
                "user_name": (
                    getattr(lv.user, "full_name", None) or
                    (lv.user.username if lv.user else None)),
            })
        data["levels"] = levels
    return data


@bp.route("/admin/approval-paths", methods=["GET"])
@api_auth_required("approvalpath.view")
def list_approval_paths(api_user):
    from app.modules.approval_config.models import ApprovalPath
    from sqlalchemy.orm import selectinload
    rows = (ApprovalPath.query
            .options(selectinload(ApprovalPath.levels))
            .order_by(ApprovalPath.name).all())
    return jsonify({
        "items": [_path_json(p) for p in rows],
        "total": len(rows),
    })


@bp.route("/admin/approval-paths/<int:pid>", methods=["GET"])
@api_auth_required("approvalpath.view")
def get_approval_path(api_user, pid):
    from app.modules.approval_config.models import ApprovalPath, ApprovalLevel
    from sqlalchemy.orm import selectinload, joinedload
    path = (ApprovalPath.query
            .options(selectinload(ApprovalPath.levels)
                     .joinedload(ApprovalLevel.role),
                     selectinload(ApprovalPath.levels)
                     .joinedload(ApprovalLevel.user))
            .filter_by(id=pid).first())
    if path is None:
        return _not_found("Approval path")
    return jsonify(_path_json(path, detail=True))


@bp.route("/admin/approval-matrix", methods=["GET"])
@api_auth_required("approvalmatrix.view")
def list_approval_matrix(api_user):
    from app.modules.approval_config.models import ApprovalMatrix
    from sqlalchemy.orm import joinedload
    rows = (ApprovalMatrix.query
            .options(joinedload(ApprovalMatrix.document_type),
                     joinedload(ApprovalMatrix.approval_path))
            .order_by(ApprovalMatrix.id).all())
    items = []
    for m in rows:
        items.append({
            "id": m.id,
            "document_type_id": m.document_type_id,
            "document_type_code": (
                m.document_type.code if m.document_type else None),
            "document_type_name": (
                m.document_type.name if m.document_type else None),
            "approval_path_id": m.approval_path_id,
            "approval_path_name": (
                m.approval_path.name if m.approval_path else None),
            "min_amount": float(m.min_amount) if m.min_amount is not None else None,
            "max_amount": float(m.max_amount) if m.max_amount is not None else None,
            "effective_from": (
                m.effective_from.isoformat() if m.effective_from else None),
            "effective_to": (
                m.effective_to.isoformat() if m.effective_to else None),
            "is_active": bool(m.is_active),
        })
    return jsonify({"items": items, "total": len(items)})
