"""Email config/templates and Audit Trail APIs for React admin."""
from flask import jsonify, request

from app.modules.api.auth import api_auth_required
from app.modules.api.pagination import resolve_page_size
from app.modules.api.routes import bp


def _bad(message, code=400):
    return jsonify({"error": "bad_request", "message": message}), code


def _not_found(label="Record"):
    return jsonify({"error": "not_found", "message": f"{label} not found."}), 404


def _validation(message, field="_"):
    return jsonify({"error": "validation", "message": message,
                    "fields": {field: message}}), 400


# ── Email configuration (SMTP) ──────────────────────────────────────────────

def _config_json(c, *, reveal_password=False):
    has_pw = bool(c.smtp_password)
    return {
        "id": c.id,
        "smtp_host": c.smtp_host,
        "smtp_port": c.smtp_port or 587,
        "smtp_username": c.smtp_username,
        # Never echo the real password; clients send a new value only when changing.
        "smtp_password": c.smtp_password if reveal_password else None,
        "has_password": has_pw,
        "use_tls": bool(c.use_tls),
        "from_email": c.from_email,
        "from_name": c.from_name,
        "is_enabled": bool(c.is_enabled),
    }


@bp.route("/admin/email-config", methods=["GET"])
@api_auth_required("emailtemplate.view")
def get_email_config(api_user):
    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    return jsonify(_config_json(EmailConfigService().get()))


@bp.route("/admin/email-config", methods=["PUT", "PATCH"])
@api_auth_required("emailtemplate.update")
def save_email_config(api_user):
    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("smtp_host", "smtp_username", "from_email", "from_name"):
        if k in p:
            v = p[k]
            fields[k] = (v.strip() if isinstance(v, str) else v) or None
    if "smtp_port" in p:
        try:
            fields["smtp_port"] = int(p["smtp_port"]) if p["smtp_port"] not in (
                None, "") else 587
        except (TypeError, ValueError):
            return _validation("smtp_port must be an integer.", "smtp_port")
    if "use_tls" in p:
        fields["use_tls"] = bool(p["use_tls"])
    if "is_enabled" in p:
        fields["is_enabled"] = bool(p["is_enabled"])
    # Only update password when a non-empty string is provided.
    if p.get("smtp_password"):
        fields["smtp_password"] = p["smtp_password"]
    cfg = EmailConfigService().update(**fields)
    return jsonify(_config_json(cfg))


@bp.route("/admin/email-config/test", methods=["POST"])
@api_auth_required("emailtemplate.update")
def test_email_config(api_user):
    """Send a short test message using current SMTP settings."""
    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService, EmailSenderService, EmailNotConfiguredError)
    p = request.get_json(silent=True) or {}
    to_email = (p.get("to_email") or "").strip()
    if not to_email:
        return _validation("to_email is required.", "to_email")
    try:
        EmailSenderService().send(
            to_email=to_email,
            subject="FMS test email",
            body_html="<p>This is a test message from Fleet Management System.</p>",
            body_text="This is a test message from Fleet Management System.",
        )
    except EmailNotConfiguredError as e:
        return _bad(str(e), 400)
    except Exception as e:
        return _bad(f"Send failed: {e}", 502)
    return jsonify({"ok": True, "message": f"Test email sent to {to_email}."})


# ── Email templates ─────────────────────────────────────────────────────────

def _tmpl_json(t, *, detail=False):
    data = {
        "id": t.id,
        "event_code": t.event_code,
        "name": t.name,
        "subject": t.subject,
        "is_active": bool(t.is_active),
    }
    if detail:
        data["body_html"] = t.body_html or ""
        data["body_text"] = t.body_text or ""
    return data


@bp.route("/admin/email-templates", methods=["GET"])
@api_auth_required("emailtemplate.view")
def list_email_templates(api_user):
    from app.modules.system_admin.models import EmailTemplate
    rows = EmailTemplate.query.order_by(EmailTemplate.event_code).all()
    return jsonify({
        "items": [_tmpl_json(t) for t in rows],
        "total": len(rows),
    })


@bp.route("/admin/email-templates/<int:tid>", methods=["GET"])
@api_auth_required("emailtemplate.view")
def get_email_template(api_user, tid):
    from app.modules.system_admin.models import EmailTemplate
    from app.extensions import db
    t = db.session.get(EmailTemplate, tid)
    if t is None:
        return _not_found("Email template")
    return jsonify(_tmpl_json(t, detail=True))


@bp.route("/admin/email-templates", methods=["POST"])
@api_auth_required("emailtemplate.create")
def create_email_template(api_user):
    from app.modules.system_admin.services.email_template_service import (
        EmailTemplateService)
    p = request.get_json(silent=True) or {}
    event_code = (p.get("event_code") or "").strip().upper()
    name = (p.get("name") or "").strip()
    subject = (p.get("subject") or "").strip()
    if not event_code:
        return _validation("event_code is required.", "event_code")
    if not name:
        return _validation("name is required.", "name")
    if not subject:
        return _validation("subject is required.", "subject")
    try:
        t = EmailTemplateService().create(
            event_code=event_code, name=name, subject=subject,
            body_html=p.get("body_html") or "",
            body_text=p.get("body_text") or "",
        )
    except Exception as e:
        return _bad(str(e), 409)
    return jsonify(_tmpl_json(t, detail=True)), 201


@bp.route("/admin/email-templates/<int:tid>", methods=["PUT", "PATCH"])
@api_auth_required("emailtemplate.update")
def update_email_template(api_user, tid):
    from app.modules.system_admin.services.email_template_service import (
        EmailTemplateService)
    p = request.get_json(silent=True) or {}
    fields = {}
    for k in ("name", "subject", "body_html", "body_text"):
        if k in p:
            fields[k] = p[k] if k.startswith("body_") else (p[k] or "").strip()
    t = EmailTemplateService().update(tid, **fields)
    if t is None:
        return _not_found("Email template")
    return jsonify(_tmpl_json(t, detail=True))


@bp.route("/admin/email-templates/<int:tid>/deactivate", methods=["POST"])
@api_auth_required("emailtemplate.delete")
def deactivate_email_template(api_user, tid):
    from app.modules.system_admin.services.email_template_service import (
        EmailTemplateService)
    EmailTemplateService().deactivate(tid)
    return jsonify({"ok": True})


# ── Audit trail ─────────────────────────────────────────────────────────────

def _audit_json(log, usernames):
    return {
        "id": log.id,
        "table_name": log.table_name,
        "record_id": log.record_id,
        "action": log.action,
        "user_id": log.user_id,
        "username": usernames.get(log.user_id) if log.user_id else None,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "ip_address": log.ip_address,
        "old_values": log.old_values,
        "new_values": log.new_values,
    }


@bp.route("/admin/audit-trail", methods=["GET"])
@api_auth_required("audittrail.view")
def list_audit_trail(api_user):
    from app.core.models.audit_log import AuditLog
    from app.extensions import db
    from app.modules.user_management.models import User

    q = AuditLog.query
    table = (request.args.get("table") or "").strip()
    action = (request.args.get("action") or "").strip().upper()
    user_id = request.args.get("user_id")
    date_from = (request.args.get("date_from") or "").strip()
    date_to = (request.args.get("date_to") or "").strip()
    search = (request.args.get("q") or "").strip().lower()

    if table:
        q = q.filter(AuditLog.table_name == table)
    if action:
        q = q.filter(AuditLog.action == action)
    if user_id:
        try:
            q = q.filter(AuditLog.user_id == int(user_id))
        except (TypeError, ValueError):
            return _validation("user_id must be an integer.", "user_id")
    if date_from:
        q = q.filter(AuditLog.timestamp >= date_from)
    if date_to:
        q = q.filter(AuditLog.timestamp <= date_to + " 23:59:59")

    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = resolve_page_size(request.args.get("page_size"),
                                      maximum=200)
    except (TypeError, ValueError):
        return _bad("page and page_size must be integers.")

    # Cap scan for safety; total from filtered query before limit.
    total = q.count()
    rows = (q.order_by(AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all())

    if search:
        filtered = []
        for log in rows:
            blob = " ".join(filter(None, [
                log.table_name, log.action, str(log.record_id or ""),
                str(log.user_id or ""), log.ip_address or "",
            ])).lower()
            if search in blob:
                filtered.append(log)
        rows = filtered

    user_ids = {log.user_id for log in rows if log.user_id}
    usernames = {}
    if user_ids:
        for u in User.query.filter(User.id.in_(user_ids)).all():
            usernames[u.id] = u.username

    tables = [
        r[0] for r in
        db.session.query(AuditLog.table_name).distinct()
        .order_by(AuditLog.table_name).all()
    ]

    return jsonify({
        "items": [_audit_json(log, usernames) for log in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "tables": tables,
        "actions": ["CREATE", "UPDATE", "DELETE"],
    })
