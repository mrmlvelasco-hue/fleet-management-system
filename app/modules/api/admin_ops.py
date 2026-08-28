"""Admin ops APIs: notifications, rules, print templates, backup, reports."""
from flask import jsonify, request

from app.extensions import db
from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp


def _iso(v):
    return v.isoformat() if v else None


# ── In-app notifications ──────────────────────────────────────────────────

@bp.route("/notifications", methods=["GET"])
@api_auth_required()
def notifications_list(api_user):
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    limit = min(request.args.get("limit", 20, type=int) or 20, 50)
    items = InAppNotificationService().list_for_user(api_user, limit=limit)
    return jsonify({
        "unread": InAppNotificationService().unread_count(api_user),
        "items": [{
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "event_code": n.event_code,
            "reference_table": n.reference_table,
            "reference_id": n.reference_id,
            "is_read": n.is_read,
            "created_at": _iso(n.created_at),
        } for n in items],
    })


@bp.route("/notifications/unread-count", methods=["GET"])
@api_auth_required()
def notifications_unread(api_user):
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    return jsonify({"count": InAppNotificationService().unread_count(api_user)})


@bp.route("/notifications/<int:nid>/read", methods=["POST"])
@api_auth_required()
def notification_read(api_user, nid):
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    InAppNotificationService().mark_read(nid, api_user)
    return jsonify({"ok": True})


@bp.route("/notifications/read-all", methods=["POST"])
@api_auth_required()
def notification_read_all(api_user):
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    InAppNotificationService().mark_all_read(api_user)
    return jsonify({"ok": True})


# ── Notification rules ────────────────────────────────────────────────────

EVENTS = [
    "submitted", "approved_level", "approved_final", "rejected",
    "returned", "resubmitted", "cancelled", "pm_due_soon", "pm_overdue",
    "registration_due_soon", "registration_overdue", "DOCUMENT_COMMENT",
]


def _rule_json(r):
    return {
        "id": r.id,
        "event_code": r.event_code,
        "channel": r.channel,
        "recipient_type": r.recipient_type,
        "role_id": r.role_id,
        "role_name": r.role.name if r.role else None,
        "user_id": r.user_id,
        "user_name": (getattr(r.user, "full_name", None) or getattr(r.user, "username", None)) if r.user else None,
        "is_active": r.is_active,
    }


@bp.route("/admin/notification-rules", methods=["GET"])
@api_auth_required("notificationrule.view")
def list_rules(api_user):
    from app.modules.system_admin.models import NotificationRule
    rows = NotificationRule.query.order_by(NotificationRule.event_code).all()
    return jsonify({"items": [_rule_json(r) for r in rows], "events": EVENTS})


@bp.route("/admin/notification-rules", methods=["POST"])
@api_auth_required("notificationrule.create")
def create_rule(api_user):
    from app.modules.system_admin.models import NotificationRule
    p = request.get_json(silent=True) or {}
    if not p.get("event_code") or not p.get("recipient_type"):
        return jsonify({"error": "validation", "message": "Event and recipient are required."}), 400
    rule = NotificationRule(
        event_code=p["event_code"],
        channel=p.get("channel") or "IN_APP",
        recipient_type=p["recipient_type"],
        role_id=int(p["role_id"]) if p.get("role_id") else None,
        user_id=int(p["user_id"]) if p.get("user_id") else None,
    )
    db.session.add(rule)
    db.session.commit()
    return jsonify(_rule_json(rule)), 201


@bp.route("/admin/notification-rules/<int:rid>/deactivate", methods=["POST"])
@api_auth_required("notificationrule.delete")
def deactivate_rule(api_user, rid):
    from app.modules.system_admin.models import NotificationRule
    rule = db.session.get(NotificationRule, rid)
    if rule is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    rule.is_active = False
    db.session.commit()
    return jsonify(_rule_json(rule))


# ── Print templates ───────────────────────────────────────────────────────

def _tpl_json(t, tokens=None):
    data = {
        "id": t.id,
        "code": t.code,
        "name": t.name,
        "description": t.description,
        "body_html": t.body_html,
        "paper_size": t.paper_size,
        "orientation": t.orientation,
    }
    if tokens is not None:
        data["tokens"] = tokens
        data["paper_sizes"] = ["A4", "LETTER", "LEGAL"]
    return data


@bp.route("/admin/print-templates", methods=["GET"])
@api_auth_required("sysparam.view")
def list_print_templates(api_user):
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    return jsonify({"items": [_tpl_json(t) for t in PrintTemplateService().list()]})


@bp.route("/admin/print-templates/<int:tid>", methods=["GET"])
@api_auth_required("sysparam.view")
def get_print_template(api_user, tid):
    from app.modules.system_admin.models import PrintTemplate
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService)
    tpl = db.session.get(PrintTemplate, tid)
    if tpl is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    return jsonify(_tpl_json(tpl, PrintTemplateService().available_tokens()))


@bp.route("/admin/print-templates/<int:tid>", methods=["PUT", "PATCH"])
@api_auth_required("sysparam.update")
def save_print_template(api_user, tid):
    from app.modules.system_admin.services.print_template_service import (
        PrintTemplateService, InvalidPaperSizeError)
    p = request.get_json(silent=True) or {}
    try:
        tpl = PrintTemplateService().update(
            tid,
            body_html=p.get("body_html"),
            paper_size=p.get("paper_size"),
            orientation=p.get("orientation"),
            name=p.get("name"),
        )
    except InvalidPaperSizeError as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    if tpl is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    db.session.commit()
    return jsonify(_tpl_json(tpl))


# ── Backup ────────────────────────────────────────────────────────────────

@bp.route("/admin/backup", methods=["GET"])
@api_auth_required("backupconfig.view")
def get_backup(api_user):
    from app.modules.system_admin.models import BackupConfig
    from app.modules.system_admin.services.backup_service import BackupService
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
    cfg = BackupConfig.query.filter_by(is_active=True).first()
    backups = BackupService().list_backups(cfg.destination_path if cfg else None)
    files = []
    for b in backups or []:
        if isinstance(b, dict):
            files.append({
                "name": b.get("name"),
                "size_bytes": b.get("size_bytes"),
                "created_at": _iso(b.get("created_at")),
            })
        else:
            files.append({"name": str(b)})
    return jsonify({
        "schedule": cfg.schedule if cfg else "MANUAL",
        "retention_days": cfg.retention_days if cfg else 30,
        "destination_path": cfg.destination_path if cfg else "",
        "mysqldump_path": SystemParameterService().get("MYSQLDUMP_PATH", "") or "",
        "files": files,
    })


@bp.route("/admin/backup", methods=["PUT", "PATCH"])
@api_auth_required("backupconfig.update")
def save_backup(api_user):
    from app.modules.system_admin.models import BackupConfig, SystemParameter
    p = request.get_json(silent=True) or {}
    cfg = BackupConfig.query.filter_by(is_active=True).first()
    if cfg is None:
        cfg = BackupConfig()
        db.session.add(cfg)
    if p.get("schedule"):
        cfg.schedule = p["schedule"]
    if p.get("retention_days") is not None:
        cfg.retention_days = int(p["retention_days"])
    if "destination_path" in p:
        cfg.destination_path = p.get("destination_path") or ""
    if "mysqldump_path" in p:
        param = SystemParameter.query.filter_by(code="MYSQLDUMP_PATH").first()
        if param is None:
            param = SystemParameter(
                code="MYSQLDUMP_PATH", value="", data_type="STRING",
                group_name="BACKUP",
                description="Full path to mysqldump.")
            db.session.add(param)
        param.value = (p.get("mysqldump_path") or "").strip()
    db.session.commit()
    return get_backup(api_user)


@bp.route("/admin/backup/test", methods=["POST"])
@api_auth_required("backupconfig.update")
def test_backup(api_user):
    from app.modules.system_admin.services.backup_service import BackupService
    p = request.get_json(silent=True) or {}
    results = BackupService().test_configuration(
        p.get("destination_path") or "",
        p.get("mysqldump_path") or None)
    return jsonify({"checks": results})


@bp.route("/admin/backup/run", methods=["POST"])
@api_auth_required("backupconfig.update")
def run_backup(api_user):
    from app.modules.system_admin.models import BackupConfig
    from app.modules.system_admin.services.backup_service import BackupService
    cfg = BackupConfig.query.filter_by(is_active=True).first()
    result = BackupService().run_backup(
        destination_path=cfg.destination_path if cfg else None,
        retention_days=cfg.retention_days if cfg else 30)
    return jsonify(result)


# ── Report config ─────────────────────────────────────────────────────────

@bp.route("/admin/report-config", methods=["GET"])
@api_auth_required("reportconfig.view")
def list_report_config(api_user):
    from app.modules.system_admin.models import ReportConfig
    from app.modules.system_admin.services.report_registry_service import (
        ReportRegistryService)
    items = ReportConfig.query.order_by(ReportConfig.report_code).all()
    available = ReportRegistryService().list_available(api_user)
    return jsonify({
        "items": [{
            "id": r.id,
            "report_code": r.report_code,
            "name": r.name,
            "description": r.description,
            "template_path": r.template_path,
            "is_active": r.is_active,
        } for r in items],
        "available": available,
    })


@bp.route("/admin/report-config", methods=["POST"])
@api_auth_required("reportconfig.update")
def create_report_config(api_user):
    from app.modules.system_admin.models import ReportConfig
    p = request.get_json(silent=True) or {}
    code = (p.get("report_code") or "").strip().upper()
    name = (p.get("name") or "").strip()
    if not code or not name:
        return jsonify({"error": "validation", "message": "Code and name are required."}), 400
    if ReportConfig.query.filter_by(report_code=code).first():
        return jsonify({"error": "validation", "message": f"Report code '{code}' already exists."}), 400
    row = ReportConfig(
        report_code=code, name=name,
        description=(p.get("description") or "").strip() or None,
        template_path=(p.get("template_path") or "").strip() or None,
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"id": row.id, "report_code": row.report_code, "name": row.name}), 201
