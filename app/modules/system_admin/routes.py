"""System Administration blueprint: System Parameters, Lookups, Company
Profile, Email Templates, Notification Rules, Audit Trail Viewer,
Dashboard Config, Backup Config, Report Config, and the notification bell
API endpoints. Thin controllers only — business logic in services."""
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, jsonify)
from flask_login import login_required, current_user

from app.core.security.decorators import require_permission
from app.core.security.registry import registry
from app.core.models.audit_log import AuditLog
from app.modules.system_admin.models import (
    SystemParameter, Lookup, EmailTemplate,
    NotificationRule, BackupConfig, ReportConfig,
    DashboardWidget, UserDashboardConfig)
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)
from app.modules.system_admin.services.lookup_service import LookupService
from app.modules.system_admin.services.company_service import (
    CompanyProfileService)
from app.modules.system_admin.services.email_template_service import (
    EmailTemplateService)
from app.modules.system_admin.services.notification_engine import (
    InAppNotificationService)
from app.extensions import db

bp = Blueprint("system_admin", __name__, url_prefix="/admin",
               template_folder="templates")

# ── Register permissions ───────────────────────────────────────────────────
for _code, _desc in [
    ("sysparam.view", "View system parameters"),
    ("sysparam.update", "Edit system parameters"),
    ("lookup.view", "View lookups"),
    ("lookup.create", "Create lookups"),
    ("lookup.update", "Update lookups"),
    ("lookup.delete", "Deactivate lookups"),
    ("company.view", "View company profile"),
    ("company.update", "Edit company profile"),
    ("emailtemplate.view", "View email templates"),
    ("emailtemplate.create", "Create email templates"),
    ("emailtemplate.update", "Update email templates"),
    ("emailtemplate.delete", "Deactivate email templates"),
    ("notificationrule.view", "View notification rules"),
    ("notificationrule.create", "Create notification rules"),
    ("notificationrule.update", "Update notification rules"),
    ("notificationrule.delete", "Deactivate notification rules"),
    ("audittrail.view", "View audit trail"),
    ("dashboardconfig.view", "View dashboard config"),
    ("dashboardconfig.update", "Update dashboard config"),
    ("backupconfig.view", "View backup config"),
    ("backupconfig.update", "Update backup config"),
    ("reportconfig.view", "View report config"),
    ("reportconfig.update", "Update report config"),
]:
    _m, _a = _code.split(".")
    registry.register(_code, _m, _a, _desc)


# ── System Parameters ──────────────────────────────────────────────────────

@bp.route("/system-parameters")
@login_required
@require_permission("sysparam.view")
def sysparam_list():
    params = (SystemParameter.query.order_by(
        SystemParameter.group_name, SystemParameter.code).all())
    return render_template("system_admin/sysparam_list.html", params=params)


@bp.route("/system-parameters/<int:param_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("sysparam.update")
def sysparam_edit(param_id):
    param = db.session.get(SystemParameter, param_id)
    if param is None or not param.is_editable:
        flash("Parameter not found or not editable.", "warning")
        return redirect(url_for("system_admin.sysparam_list"))
    if request.method == "POST":
        SystemParameterService().set(param.code, request.form["value"])
        flash(f"Parameter '{param.code}' updated.", "success")
        return redirect(url_for("system_admin.sysparam_list"))
    return render_template("system_admin/sysparam_edit.html", param=param)


# ── Lookup Maintenance ─────────────────────────────────────────────────────

@bp.route("/lookups")
@login_required
@require_permission("lookup.view")
def lookup_list():
    filter_type = request.args.get("type", "")
    query = Lookup.query.order_by(Lookup.lookup_type, Lookup.sort_order)
    if filter_type:
        query = query.filter_by(lookup_type=filter_type)
    items = query.all()
    types = db.session.query(Lookup.lookup_type).distinct().all()
    return render_template("system_admin/lookup_list.html", items=items,
                           types=[t[0] for t in types],
                           filter_type=filter_type)


@bp.route("/lookups/new", methods=["GET", "POST"])
@login_required
@require_permission("lookup.create")
def lookup_new():
    if request.method == "POST":
        LookupService().create(
            lookup_type=request.form["lookup_type"].strip().upper(),
            code=request.form["code"].strip().upper(),
            description=request.form["description"],
            sort_order=int(request.form.get("sort_order", 0)))
        flash("Lookup created.", "success")
        return redirect(url_for("system_admin.lookup_list"))
    return render_template("system_admin/lookup_form.html", item=None,
                           title="New Lookup")


@bp.route("/lookups/<int:lookup_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("lookup.update")
def lookup_edit(lookup_id):
    item = db.session.get(Lookup, lookup_id)
    if item is None:
        flash("Lookup not found.", "warning")
        return redirect(url_for("system_admin.lookup_list"))
    if request.method == "POST":
        LookupService().update(lookup_id,
                               description=request.form["description"],
                               sort_order=int(request.form.get("sort_order", 0)))
        flash("Lookup updated.", "success")
        return redirect(url_for("system_admin.lookup_list"))
    return render_template("system_admin/lookup_form.html", item=item,
                           title=f"Edit Lookup — {item.code}")


@bp.route("/lookups/<int:lookup_id>/deactivate", methods=["POST"])
@login_required
@require_permission("lookup.delete")
def lookup_deactivate(lookup_id):
    LookupService().deactivate(lookup_id)
    flash("Lookup deactivated.", "info")
    return redirect(url_for("system_admin.lookup_list"))


# ── Company Profile ────────────────────────────────────────────────────────

@bp.route("/company-profile", methods=["GET", "POST"])
@login_required
@require_permission("company.view")
def company_profile():
    svc = CompanyProfileService()
    profile = svc.get()
    if request.method == "POST":
        if not current_user.has_permission("company.update"):
            flash("You do not have permission to update the company profile.",
                  "danger")
            return redirect(url_for("system_admin.company_profile"))
        fields = ["company_name", "address_line1", "address_line2",
                  "city", "country", "phone", "email", "tin"]
        svc.save(**{f: request.form.get(f, "") for f in fields})
        flash("Company profile updated.", "success")
        return redirect(url_for("system_admin.company_profile"))
    return render_template("system_admin/company_profile.html",
                           profile=profile)


# ── Email Templates ────────────────────────────────────────────────────────

@bp.route("/email-templates")
@login_required
@require_permission("emailtemplate.view")
def email_template_list():
    items = EmailTemplate.query.order_by(EmailTemplate.event_code).all()
    return render_template("system_admin/email_template_list.html", items=items)


@bp.route("/email-templates/new", methods=["GET", "POST"])
@login_required
@require_permission("emailtemplate.create")
def email_template_new():
    if request.method == "POST":
        EmailTemplateService().create(
            event_code=request.form["event_code"],
            name=request.form["name"],
            subject=request.form["subject"],
            body_html=request.form.get("body_html", ""),
            body_text=request.form.get("body_text", ""))
        flash("Email template created.", "success")
        return redirect(url_for("system_admin.email_template_list"))
    return render_template("system_admin/email_template_form.html",
                           item=None, title="New Email Template")


@bp.route("/email-templates/<int:tmpl_id>/edit", methods=["GET", "POST"])
@login_required
@require_permission("emailtemplate.update")
def email_template_edit(tmpl_id):
    item = db.session.get(EmailTemplate, tmpl_id)
    if item is None:
        flash("Template not found.", "warning")
        return redirect(url_for("system_admin.email_template_list"))
    if request.method == "POST":
        EmailTemplateService().update(
            tmpl_id, name=request.form["name"],
            subject=request.form["subject"],
            body_html=request.form.get("body_html", ""),
            body_text=request.form.get("body_text", ""))
        flash("Email template updated.", "success")
        return redirect(url_for("system_admin.email_template_list"))
    return render_template("system_admin/email_template_form.html",
                           item=item, title=f"Edit — {item.name}")


@bp.route("/email-templates/<int:tmpl_id>/deactivate", methods=["POST"])
@login_required
@require_permission("emailtemplate.delete")
def email_template_deactivate(tmpl_id):
    EmailTemplateService().deactivate(tmpl_id)
    flash("Email template deactivated.", "info")
    return redirect(url_for("system_admin.email_template_list"))


# ── Notification Rules ─────────────────────────────────────────────────────

@bp.route("/notification-rules")
@login_required
@require_permission("notificationrule.view")
def notification_rule_list():
    items = NotificationRule.query.order_by(
        NotificationRule.event_code).all()
    return render_template("system_admin/notification_rule_list.html",
                           items=items)


@bp.route("/notification-rules/new", methods=["GET", "POST"])
@login_required
@require_permission("notificationrule.create")
def notification_rule_new():
    from app.modules.user_management.repository import (
        RoleRepository, UserRepository)
    roles = RoleRepository().list()
    users = UserRepository().list()
    if request.method == "POST":
        rule = NotificationRule(
            event_code=request.form["event_code"],
            channel=request.form["channel"],
            recipient_type=request.form["recipient_type"],
            role_id=int(request.form["role_id"]) if request.form.get("role_id") else None,
            user_id=int(request.form["user_id"]) if request.form.get("user_id") else None)
        db.session.add(rule)
        db.session.commit()
        flash("Notification rule created.", "success")
        return redirect(url_for("system_admin.notification_rule_list"))
    return render_template("system_admin/notification_rule_form.html",
                           item=None, roles=roles, users=users,
                           title="New Notification Rule")


@bp.route("/notification-rules/<int:rule_id>/deactivate", methods=["POST"])
@login_required
@require_permission("notificationrule.delete")
def notification_rule_deactivate(rule_id):
    rule = db.session.get(NotificationRule, rule_id)
    if rule:
        rule.is_active = False
        db.session.commit()
    flash("Notification rule deactivated.", "info")
    return redirect(url_for("system_admin.notification_rule_list"))


# ── Audit Trail Viewer ─────────────────────────────────────────────────────

@bp.route("/audit-trail")
@login_required
@require_permission("audittrail.view")
def audit_trail():
    q = AuditLog.query
    if request.args.get("table"):
        q = q.filter(AuditLog.table_name == request.args["table"])
    if request.args.get("action"):
        q = q.filter(AuditLog.action == request.args["action"])
    if request.args.get("user_id"):
        q = q.filter(AuditLog.user_id == int(request.args["user_id"]))
    if request.args.get("date_from"):
        q = q.filter(AuditLog.timestamp >= request.args["date_from"])
    if request.args.get("date_to"):
        q = q.filter(AuditLog.timestamp <= request.args["date_to"] + " 23:59:59")
    logs = q.order_by(AuditLog.id.desc()).limit(500).all()
    tables = db.session.query(AuditLog.table_name).distinct().all()
    return render_template("system_admin/audit_trail.html", logs=logs,
                           tables=[t[0] for t in tables],
                           filters=request.args)


# ── Dashboard Config ───────────────────────────────────────────────────────

@bp.route("/dashboard-config", methods=["GET", "POST"])
@login_required
@require_permission("dashboardconfig.view")
def dashboard_config():
    widgets = DashboardWidget.query.order_by(
        DashboardWidget.sort_order).all()
    if request.method == "POST":
        if not current_user.has_permission("dashboardconfig.update"):
            flash("Permission denied.", "danger")
            return redirect(url_for("system_admin.dashboard_config"))
        for w in widgets:
            visible = w.code in request.form.getlist("visible_widgets")
            cfg = UserDashboardConfig.query.filter_by(
                user_id=current_user.id, widget_code=w.code).first()
            if cfg is None:
                db.session.add(UserDashboardConfig(
                    user_id=current_user.id,
                    widget_code=w.code, is_visible=visible))
            else:
                cfg.is_visible = visible
        db.session.commit()
        flash("Dashboard configuration saved.", "success")
        return redirect(url_for("system_admin.dashboard_config"))
    user_configs = {c.widget_code: c.is_visible
                   for c in UserDashboardConfig.query.filter_by(
                       user_id=current_user.id).all()}
    return render_template("system_admin/dashboard_config.html",
                           widgets=widgets, user_configs=user_configs)


# ── Backup Config ──────────────────────────────────────────────────────────

@bp.route("/backup-config", methods=["GET", "POST"])
@login_required
@require_permission("backupconfig.view")
def backup_config():
    cfg = BackupConfig.query.filter_by(is_active=True).first()
    if request.method == "POST":
        if not current_user.has_permission("backupconfig.update"):
            flash("Permission denied.", "danger")
            return redirect(url_for("system_admin.backup_config"))
        if cfg is None:
            cfg = BackupConfig()
            db.session.add(cfg)
        cfg.schedule = request.form.get("schedule", "MANUAL")
        cfg.retention_days = int(request.form.get("retention_days", 30))
        cfg.destination_path = request.form.get("destination_path", "")
        db.session.commit()
        flash("Backup configuration saved.", "success")
        return redirect(url_for("system_admin.backup_config"))
    return render_template("system_admin/backup_config.html", cfg=cfg)


# ── Report Config ──────────────────────────────────────────────────────────

@bp.route("/report-config")
@login_required
@require_permission("reportconfig.view")
def report_config_list():
    items = ReportConfig.query.order_by(ReportConfig.report_code).all()
    return render_template("system_admin/report_config_list.html", items=items)


# ── Notification bell API ──────────────────────────────────────────────────

@bp.route("/notifications/unread-count")
@login_required
def notifications_unread_count():
    count = InAppNotificationService().unread_count(current_user)
    return jsonify(count=count)


@bp.route("/notifications/recent")
@login_required
def notifications_recent():
    items = InAppNotificationService().list_for_user(current_user, limit=5)
    return jsonify(notifications=[{
        "id": n.id, "title": n.title, "message": n.message,
        "is_read": n.is_read, "created_at": n.created_at.isoformat()
    } for n in items])


@bp.route("/notifications/<int:notif_id>/mark-read", methods=["POST"])
@login_required
def notification_mark_read(notif_id):
    InAppNotificationService().mark_read(notif_id, current_user)
    return jsonify(ok=True)


@bp.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def notification_mark_all_read():
    InAppNotificationService().mark_all_read(current_user)
    return jsonify(ok=True)
