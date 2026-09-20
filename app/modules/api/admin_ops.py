"""Admin ops APIs: notifications, rules, print templates, backup, reports."""
from flask import jsonify, request
from datetime import datetime

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
    print(
        "DEBUG MOBILE NOTIFICATIONS:",
        [
            {
                "id": n.id,
                "title": n.title,
                "event_code": n.event_code,
                "reference_table": n.reference_table,
                "reference_id": n.reference_id,
                "is_read": n.is_read,
            }
            for n in items
        ],
        flush=True,
    )
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


# ── Dashboard widget visibility ───────────────────────────────────────────

@bp.route("/admin/dashboard-config", methods=["GET"])
@api_auth_required("dashboardconfig.view")
def get_dashboard_config(api_user):
    from app.modules.system_admin.models import (
        DashboardWidget, UserDashboardConfig)
    widgets = DashboardWidget.query.order_by(DashboardWidget.sort_order).all()
    user_map = {
        c.widget_code: c.is_visible
        for c in UserDashboardConfig.query.filter_by(user_id=api_user.id).all()
    }
    return jsonify({
        "items": [{
            "code": w.code,
            "label": w.label,
            "icon": w.icon,
            "default_visible": w.default_visible,
            "visible": user_map.get(w.code, w.default_visible),
        } for w in widgets]
    })


@bp.route("/admin/dashboard-config", methods=["PUT", "PATCH"])
@api_auth_required("dashboardconfig.update")
def save_dashboard_config(api_user):
    from app.modules.system_admin.models import (
        DashboardWidget, UserDashboardConfig)
    p = request.get_json(silent=True) or {}
    visible_codes = set(p.get("visible") or [])
    widgets = DashboardWidget.query.all()
    for w in widgets:
        visible = w.code in visible_codes
        cfg = UserDashboardConfig.query.filter_by(
            user_id=api_user.id, widget_code=w.code).first()
        if cfg is None:
            db.session.add(UserDashboardConfig(
                user_id=api_user.id, widget_code=w.code, is_visible=visible))
        else:
            cfg.is_visible = visible
    db.session.commit()
    return get_dashboard_config(api_user)


# ── Data quality ──────────────────────────────────────────────────────────

def _dq_branch(b):
    return {
        "rank": b.get("rank"),
        "branch_id": b["branch"].id if b.get("branch") else None,
        "branch_name": b.get("branch_name"),
        "vehicles": b.get("vehicles"),
        "complete": b.get("complete"),
        "incomplete": b.get("incomplete"),
        "score": b.get("score"),
        "rating": b.get("rating"),
    }


@bp.route("/admin/data-quality", methods=["GET"])
@api_auth_required("dataquality.view")
def get_data_quality(api_user):
    from app.core.data_quality_service import DataQualityService
    svc = DataQualityService()
    branch_id = request.args.get("branch_id", type=int)
    summary = svc.summary(user=api_user)
    branches = [_dq_branch(b) for b in svc.branch_scorecard(user=api_user)]
    fields = []
    for f in svc.field_completion(user=api_user):
        fields.append({
            "label": f["label"],
            "group": f["group"],
            "filled": f["filled"],
            "missing": f["missing"],
            "rate": f["rate"],
            "rating": f["rating"],
            "is_required": f["is_required"],
        })
    gaps = []
    if branch_id:
        for row in svc.vehicles_with_gaps(branch_id=branch_id, user=api_user):
            v = row["vehicle"]
            missing = row.get("missing") or []
            labels = []
            for m in missing:
                labels.append(getattr(m, "label", None) or str(m))
            gaps.append({
                "id": getattr(v, "id", None),
                "plate_number": getattr(v, "plate_number", None),
                "score": row.get("score"),
                "rating": row.get("rating"),
                "missing": labels,
            })
    return jsonify({
        "summary": {
            "score": summary.get("score"),
            "rating": summary.get("rating"),
            "vehicles": summary.get("vehicles"),
            "incomplete": summary.get("incomplete"),
            "missing_points": summary.get("missing_points"),
            "common_missing": summary.get("common_missing") or [],
        },
        "branches": branches,
        "fields": fields,
        "gaps": gaps,
        "selected_branch_id": branch_id,
    })


@bp.route("/admin/data-quality/settings", methods=["GET"])
@api_auth_required("dataquality.manage")
def get_dq_settings(api_user):
    from app.core.data_quality_service import DataQualityField, DataQualityService, FIELD_GROUPS
    DataQualityService().sync_fields()
    fields = (DataQualityField.query.filter_by(is_active=True)
              .order_by(DataQualityField.sort_order).all())
    grouped = {}
    for f in fields:
        grouped.setdefault(f.field_group, []).append({
            "id": f.id,
            "field_name": f.field_name,
            "label": f.label,
            "is_required": f.is_required,
            "include_in_score": f.include_in_score,
            "weight": f.weight,
        })
    order = [g for g in FIELD_GROUPS if g in grouped]
    for g in grouped:
        if g not in order:
            order.append(g)
    return jsonify({
        "groups": [{"group": g, "fields": grouped[g]} for g in order],
        "total_weight": sum(f.weight for f in fields if f.include_in_score),
    })


@bp.route("/admin/data-quality/settings", methods=["PUT", "PATCH"])
@api_auth_required("dataquality.manage")
def save_dq_settings(api_user):
    from app.core.data_quality_service import DataQualityField
    payload = request.get_json(silent=True) or {}
    updates = {int(u["id"]): u for u in (payload.get("fields") or []) if u.get("id")}
    changed = 0
    for field in DataQualityField.query.all():
        u = updates.get(field.id)
        if not u:
            continue
        inc = bool(u.get("include_in_score"))
        req = bool(u.get("is_required"))
        try:
            weight = max(1, min(100, int(u.get("weight", field.weight))))
        except (TypeError, ValueError):
            weight = field.weight
        if (inc != field.include_in_score or req != field.is_required
                or weight != field.weight):
            field.include_in_score = inc
            field.is_required = req
            field.weight = weight
            changed += 1
    db.session.commit()
    return jsonify({"changed": changed})


# ── Scheduled reports ─────────────────────────────────────────────────────

def _sched_json(s):
    return {
        "id": s.id,
        "name": s.name,
        "report_code": s.report_code,
        "frequency": s.frequency,
        "recipients": s.recipients,
        "last_run_at": _iso(s.last_run_at),
        "next_run_at": _iso(s.next_run_at),
        "last_run_status": s.last_run_status,
    }


@bp.route("/admin/scheduled-reports", methods=["GET"])
@api_auth_required("reportconfig.view")
def list_scheduled_reports(api_user):
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    from app.core.reporting.generators import REPORT_GENERATORS
    items = ScheduledReportService().list_all()
    return jsonify({
        "items": [_sched_json(s) for s in items],
        "report_choices": sorted(REPORT_GENERATORS.keys()),
    })


@bp.route("/admin/scheduled-reports", methods=["POST"])
@api_auth_required("reportconfig.update")
def create_scheduled_report(api_user):
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    p = request.get_json(silent=True) or {}
    try:
        item = ScheduledReportService().create(
            name=(p.get("name") or "").strip(),
            report_code=p.get("report_code"),
            frequency=p.get("frequency") or "WEEKLY",
            recipients=(p.get("recipients") or "").strip(),
            filters=p.get("filters") or None)
    except ValueError as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    return jsonify(_sched_json(item)), 201


@bp.route("/admin/scheduled-reports/<int:sid>/delete", methods=["POST"])
@api_auth_required("reportconfig.update")
def delete_scheduled_report(api_user, sid):
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    ScheduledReportService().delete(sid)
    return jsonify({"ok": True})


@bp.route("/admin/scheduled-reports/<int:sid>/run-now", methods=["POST"])
@api_auth_required("reportconfig.update")
def run_scheduled_report(api_user, sid):
    from datetime import datetime, timezone
    from app.modules.system_admin.models import ScheduledReport
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    item = db.session.get(ScheduledReport, sid)
    if item is None:
        return jsonify({"error": "not_found", "message": "Not found."}), 404
    item.next_run_at = datetime.now(timezone.utc)
    db.session.commit()
    sent, failed = ScheduledReportService().run_due(only_id=sid)
    return jsonify({"sent": sent, "failed": failed})


# ── Custom reports ────────────────────────────────────────────────────────

def _cr_json(r):
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "data_source": r.data_source,
        "fields": r.fields,
        "filters": r.filters,
        "sort_key": r.sort_key,
        "sort_dir": r.sort_dir,
        "row_limit": r.row_limit,
        "default_recipients": r.default_recipients,
    }


@bp.route("/admin/custom-reports", methods=["GET"])
@api_auth_required("customreport.view")
def list_custom_reports(api_user):
    from app.modules.system_admin.services.custom_report_service import (
        CustomReportService)
    from app.core.reporting.report_builder import DATA_SOURCES
    items = CustomReportService().list()
    sources = [{
        "key": k,
        "label": s.label,
        "description": s.description,
        "fields": [{"key": f.key, "label": f.label, "kind": f.kind}
                   for f in s.fields.values()],
    } for k, s in DATA_SOURCES.items()]
    return jsonify({"items": [_cr_json(r) for r in items], "sources": sources})



@bp.route("/admin/custom-reports/preview", methods=["POST"])
@api_auth_required("customreport.manage")
def preview_custom_report(api_user):
    from app.core.reporting.report_builder import run_report, ReportBuilderError
    p = request.get_json(silent=True) or {}
    try:
        result = run_report(
            p.get("data_source") or "",
            p.get("fields") or [],
            filters=p.get("filters") or [],
            sort_key=p.get("sort_key"),
            sort_dir=p.get("sort_dir") or "asc",
            limit=min(int(p.get("row_limit") or 50), 50),
            user=api_user)
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400
    rows = result.get("rows") or []
    # normalize rows to list-of-lists of strings for the preview table
    if rows and isinstance(rows[0], dict):
        cols = result.get("columns") or [{"key": k} for k in rows[0].keys()]
        keys = [c.get("key") if isinstance(c, dict) else str(c) for c in cols]
        norm = [[("" if r.get(k) is None else str(r.get(k))) for k in keys] for r in rows]
    else:
        norm = [[("" if v is None else str(v)) for v in row] for row in rows]
    return jsonify({
        "ok": True,
        "columns": result.get("columns") or [],
        "rows": norm,
        "row_count": result.get("row_count", len(norm)),
    })

@bp.route("/admin/custom-reports", methods=["POST"])
@api_auth_required("customreport.manage")
def create_custom_report(api_user):
    from app.modules.system_admin.services.custom_report_service import (
        CustomReportService, ReportBuilderError)
    p = request.get_json(silent=True) or {}
    try:
        report = CustomReportService().create(
            name=p.get("name"),
            data_source=p.get("data_source"),
            fields=p.get("fields") or [],
            filters=p.get("filters") or [],
            sort_key=p.get("sort_key"),
            sort_dir=p.get("sort_dir") or "asc",
            description=p.get("description"),
            row_limit=p.get("row_limit") or 1000,
            default_recipients=p.get("default_recipients"),
            user=api_user)
    except Exception as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    return jsonify(_cr_json(report)), 201


@bp.route("/admin/custom-reports/<int:rid>", methods=["GET"])
@api_auth_required("customreport.view")
def get_custom_report(api_user, rid):
    from app.modules.system_admin.services.custom_report_service import (
        CustomReportService)
    report = CustomReportService().get_by_id(rid)
    if report is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_cr_json(report))


@bp.route("/admin/custom-reports/<int:rid>/run", methods=["POST", "GET"])
@api_auth_required("customreport.view")
def run_custom_report(api_user, rid):
    from app.modules.system_admin.services.custom_report_service import (
        CustomReportService)
    report = CustomReportService().get_by_id(rid)
    if report is None:
        return jsonify({"error": "not_found"}), 404
    try:
        result = CustomReportService().run(report, user=api_user)
    except Exception as exc:
        return jsonify({"error": "validation", "message": str(exc)}), 400
    # result typically {columns, rows} or similar
    if isinstance(result, dict):
        return jsonify(result)
    return jsonify({"rows": result})


@bp.route("/admin/custom-reports/<int:rid>/delete", methods=["POST"])
@api_auth_required("customreport.manage")
def delete_custom_report(api_user, rid):
    from app.modules.system_admin.services.custom_report_service import (
        CustomReportService)
    CustomReportService().deactivate(rid)
    return jsonify({"ok": True})

# ── Fleet Broadcasts ──────────────────────────────────────────────────────

def _broadcast_iso(v):
    return v.isoformat() if v else None


def _broadcast_json(row):
    return {
        "id": row.id,
        "broadcast_no": row.broadcast_no,
        "broadcast_type": row.broadcast_type,
        "category": row.category,
        "title": row.title,
        "message": row.message,
        "priority": row.priority,
        "status": row.status,
        "effective_date": _broadcast_iso(row.effective_date),
        "expiry_date": _broadcast_iso(row.expiry_date),
        "created_by": row.created_by,
        "created_by_name": (
            getattr(row.creator, "full_name", None)
            or getattr(row.creator, "username", None)
            if row.creator else None
        ),
        "published_by": row.published_by,
        "published_by_name": (
            getattr(row.publisher, "full_name", None)
            or getattr(row.publisher, "username", None)
            if row.publisher else None
        ),
        "published_at": _broadcast_iso(row.published_at),
        "recipients": [
            {
                "id": r.id,
                "recipient_type": r.recipient_type,
                "role_id": r.role_id,
                "role_name": r.role.name if r.role else None,
                "user_id": r.user_id,
                "user_name": (
                    getattr(r.user, "full_name", None)
                    or getattr(r.user, "username", None)
                    if r.user else None
                ),
                "branch_id": r.branch_id,
                "department_id": r.department_id,
            }
            for r in row.recipients
        ],
    }


def _broadcast_datetime(value):
    if not value:
        return None

    value = str(value).strip()

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"Invalid date/time value '{value}'. "
            "Use ISO 8601 format."
        )


@bp.route("/admin/fleet-broadcasts", methods=["GET"])
@api_auth_required("fleetbroadcast.view")
def list_fleet_broadcasts(api_user):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    status = request.args.get("status")
    rows = FleetBroadcastService().list(status=status)

    return jsonify({
        "items": [_broadcast_json(row) for row in rows]
    })


@bp.route("/admin/fleet-broadcasts/<int:bid>", methods=["GET"])
@api_auth_required("fleetbroadcast.view")
def get_fleet_broadcast(api_user, bid):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    row = FleetBroadcastService().get(bid)

    if row is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    return jsonify(_broadcast_json(row))


@bp.route("/admin/fleet-broadcasts", methods=["POST"])
@api_auth_required("fleetbroadcast.create")
def create_fleet_broadcast(api_user):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    p = request.get_json(silent=True) or {}

    try:
        row = FleetBroadcastService().create(
            user=api_user,
            broadcast_no=p.get("broadcast_no"),
            broadcast_type=p.get("broadcast_type") or "ANNOUNCEMENT",
            category=p.get("category") or "GENERAL",
            title=p.get("title"),
            message=p.get("message"),
            priority=p.get("priority") or "NORMAL",
            effective_date=_broadcast_datetime(
                p.get("effective_date")
            ) if p.get("effective_date") else None,
            expiry_date=_broadcast_datetime(
                p.get("expiry_date")
            ) if p.get("expiry_date") else None,
            recipients=p.get("recipients") or [],
        )
    except ValueError as exc:
        return jsonify({
            "error": "validation",
            "message": str(exc)
        }), 400

    return jsonify(_broadcast_json(row)), 201


@bp.route("/admin/fleet-broadcasts/<int:bid>",
           methods=["PUT", "PATCH"])
@api_auth_required("fleetbroadcast.update")
def update_fleet_broadcast(api_user, bid):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    p = request.get_json(silent=True) or {}

    try:
        row = FleetBroadcastService().update(
            bid,
            broadcast_type=p.get("broadcast_type"),
            category=p.get("category"),
            title=p.get("title"),
            message=p.get("message"),
            priority=p.get("priority"),
            effective_date=_broadcast_datetime(
                p.get("effective_date")
            ) if p.get("effective_date") else None,
            expiry_date=_broadcast_datetime(
                p.get("expiry_date")
            ) if p.get("expiry_date") else None,
            recipients=p.get("recipients")
            if "recipients" in p else None,
        )
    except ValueError as exc:
        return jsonify({
            "error": "validation",
            "message": str(exc)
        }), 400

    if row is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    return jsonify(_broadcast_json(row))


@bp.route("/admin/fleet-broadcasts/<int:bid>/publish",
           methods=["POST"])
@api_auth_required("fleetbroadcast.update")
def publish_fleet_broadcast(api_user, bid):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    try:
        row = FleetBroadcastService().publish(
            bid,
            user=api_user
        )
    except ValueError as exc:
        return jsonify({
            "error": "validation",
            "message": str(exc)
        }), 400

    if row is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    return jsonify(_broadcast_json(row))


@bp.route("/fleet-broadcasts/<int:bid>/acknowledge", methods=["POST"])
@api_auth_required("fleetbroadcast.acknowledge")
def acknowledge_fleet_broadcast(api_user, bid):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    try:
        ack = FleetBroadcastService().acknowledge(
            bid,
            user=api_user
        )
    except ValueError as exc:
        return jsonify({
            "error": "validation",
            "message": str(exc)
        }), 400

    if ack is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    return jsonify({
        "id": ack.id,
        "broadcast_id": ack.broadcast_id,
        "user_id": ack.user_id,
        "acknowledged_at": _broadcast_iso(
            ack.acknowledged_at
        ),
    })


@bp.route("/admin/fleet-broadcasts/<int:bid>/acknowledgements",
           methods=["GET"])
@api_auth_required("fleetbroadcast.view")
def list_fleet_broadcast_acknowledgements(api_user, bid):
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )

    row = FleetBroadcastService().get(bid)

    if row is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    items = FleetBroadcastService().acknowledgements(bid)

    def _role_name(user):
        # First role only: this table is a quick organizational lookup,
        # not a full permission audit -- a user's primary role reads
        # better in one column than every role they hold.
        if not user or not user.roles:
            return None
        return user.roles[0].name

    return jsonify({
        "broadcast_id": bid,
        "items": [
            {
                "id": ack.id,
                "user_id": ack.user_id,
                "user_name": (
                    getattr(ack.user, "full_name", None)
                    or getattr(ack.user, "username", None)
                    if ack.user else None
                ),
                "employee_id": getattr(ack.user, "employee_id", None) if ack.user else None,
                "role": _role_name(ack.user),
                "department": (
                    ack.user.department.name
                    if ack.user and ack.user.department else None
                ),
                "branch": (
                    ack.user.branch.name
                    if ack.user and ack.user.branch else None
                ),
                "acknowledged_at": _broadcast_iso(
                    ack.acknowledged_at
                ),
            }
            for ack in items
        ]
    })

@bp.route("/admin/fleet-broadcasts/<int:bid>/unacknowledged", methods=["GET"])
@api_auth_required("fleetbroadcast.view")
def list_fleet_broadcast_unacknowledged(api_user, bid):
    """Who is in this broadcast's audience but has not acknowledged it."""
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService
    )
    from app.modules.system_admin.models import InAppNotification
    from app.modules.user_management.models import User

    row = FleetBroadcastService().get(bid)

    if row is None:
        return jsonify({
            "error": "not_found",
            "message": "Fleet broadcast not found."
        }), 404

    acked_ids = {
        a.user_id for a in FleetBroadcastService().acknowledgements(bid)
    }
    notes_by_user = {
        n.user_id: n
        for n in InAppNotification.query.filter_by(
            reference_table="fleet_broadcasts", reference_id=bid,
        ).all()
    }

    service = FleetBroadcastService()
    items = []
    for user in User.query.filter_by(is_active=True).all():
        if user.id in acked_ids:
            continue
        if not service.user_matches_broadcast_audience(row, user):
            continue

        note = notes_by_user.get(user.id)
        if note is None:
            status = "NOT_ACKNOWLEDGED"
        elif note.is_read:
            status = "READ"
        else:
            status = "UNREAD"

        role_list = user.roles or []
        items.append({
            "user_id": user.id,
            "user_name": user.full_name or user.username,
            "employee_id": user.employee_id,
            "role": role_list[0].name if role_list else None,
            "department": user.department.name if user.department else None,
            "branch": user.branch.name if user.branch else None,
            "notification_sent_at": _broadcast_iso(note.created_at) if note else None,
            "last_seen_at": _broadcast_iso(note.read_at) if note else None,
            "status": status,
        })

    return jsonify({"broadcast_id": bid, "items": items})

# ── Fleet Broadcasts: User Inbox ─────────────────────────────────────────

def _user_can_receive_broadcast(broadcast, user):
    """Return True when the current user belongs to a broadcast audience.

    Delegates to FleetBroadcastService, which is also what decides who
    gets notified on publish. This function used to keep its own copy of
    the same rule, missing the is_active checks the service's version
    has -- meaning a deactivated user or role could still see a broadcast
    here even though they would never have been notified of it.
    """
    from app.modules.system_admin.services.fleet_broadcast_service import (
        FleetBroadcastService)
    return FleetBroadcastService().user_matches_broadcast_audience(
        broadcast, user)


def _user_broadcast_json(row, user):
    from app.modules.system_admin.models import (
        FleetBroadcastAcknowledgement,
        InAppNotification,
    )

    ack = FleetBroadcastAcknowledgement.query.filter_by(
        broadcast_id=row.id,
        user_id=user.id,
    ).first()

    notification = InAppNotification.query.filter_by(
        user_id=user.id,
        reference_table="fleet_broadcasts",
        reference_id=row.id,
    ).order_by(
        InAppNotification.id.desc()
    ).first()

    return {
        "id": row.id,
        "broadcast_no": row.broadcast_no,
        "broadcast_type": row.broadcast_type,
        "category": row.category,
        "title": row.title,
        "message": row.message,
        "priority": row.priority,
        "status": row.status,
        "effective_date": _broadcast_iso(row.effective_date),
        "expiry_date": _broadcast_iso(row.expiry_date),
        "published_at": _broadcast_iso(row.published_at),
        "is_read": bool(notification.is_read) if notification else False,
        "read_at": (
            _broadcast_iso(notification.read_at)
            if notification else None
        ),
        "is_acknowledged": ack is not None,
        "acknowledged_at": (
            _broadcast_iso(ack.acknowledged_at)
            if ack else None
        ),
    }


@bp.route("/my/fleet-broadcasts", methods=["GET"])
@api_auth_required()
def list_my_fleet_broadcasts(api_user):
    """Return published, active broadcasts addressed to the current user."""
    from app.modules.system_admin.models import FleetBroadcast

    rows = FleetBroadcast.query.filter_by(
        status="PUBLISHED", is_active=True
    ).order_by(
        FleetBroadcast.published_at.desc(),
        FleetBroadcast.id.desc(),
    ).all()

    now = datetime.now()
    items = []

    for row in rows:
        if row.effective_date and row.effective_date > now:
            continue
        if row.expiry_date and row.expiry_date < now:
            continue
        if not _user_can_receive_broadcast(row, api_user):
            continue
        items.append(_user_broadcast_json(row, api_user))

    return jsonify({"items": items})


@bp.route("/my/fleet-broadcasts/<int:bid>", methods=["GET"])
@api_auth_required()
def get_my_fleet_broadcast(api_user, bid):
    """Return one published, active broadcast addressed to the current user."""
    from app.modules.system_admin.models import FleetBroadcast

    row = db.session.get(FleetBroadcast, bid)

    if row is None or row.status != "PUBLISHED":
        return jsonify({
            "error": "not_found",
            "message": "Fleet Broadcast not found.",
        }), 404

    if not _user_can_receive_broadcast(row, api_user):
        return jsonify({
            "error": "not_found",
            "message": "Fleet Broadcast not found.",
        }), 404

    now = datetime.now()
    if row.effective_date and row.effective_date > now:
        return jsonify({
            "error": "not_found",
            "message": "Fleet Broadcast is not yet effective.",
        }), 404
    if row.expiry_date and row.expiry_date < now:
        return jsonify({
            "error": "not_found",
            "message": "Fleet Broadcast has expired.",
        }), 404

    return jsonify(_user_broadcast_json(row, api_user))


@bp.route("/fleet-broadcasts/<int:bid>/ack-status", methods=["GET"])
@api_auth_required("fleetbroadcast.acknowledge")
def fleet_broadcast_ack_status(api_user, bid):
    """Return acknowledgement status for the current user."""
    from app.modules.system_admin.models import (
        FleetBroadcast,
        FleetBroadcastAcknowledgement,
    )

    row = db.session.get(FleetBroadcast, bid)

    if row is None or not _user_can_receive_broadcast(row, api_user):
        return jsonify({
            "error": "not_found",
            "message": "Fleet Broadcast not found.",
        }), 404

    ack = FleetBroadcastAcknowledgement.query.filter_by(
        broadcast_id=bid,
        user_id=api_user.id,
    ).first()

    if ack is None:
        return jsonify({
            "id": None,
            "broadcast_id": bid,
            "user_id": api_user.id,
            "acknowledged_at": None,
            "acknowledged": False,
        })

    return jsonify({
        "id": ack.id,
        "broadcast_id": ack.broadcast_id,
        "user_id": ack.user_id,
        "acknowledged_at": _broadcast_iso(ack.acknowledged_at),
        "acknowledged": True,
    })
