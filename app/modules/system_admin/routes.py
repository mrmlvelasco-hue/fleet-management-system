"""System Administration blueprint: System Parameters, Lookups, Company
Profile, Email Templates, Notification Rules, Audit Trail Viewer,
Dashboard Config, Backup Config, Report Config, and the notification bell
API endpoints. Thin controllers only — business logic in services."""
import os
from datetime import datetime
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, jsonify, current_app)
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


# ── Email Configuration (SMTP) ──────────────────────────────────────────────

@bp.route("/email-config", methods=["GET", "POST"])
@login_required
@require_permission("emailtemplate.view")
def email_config():
    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    svc = EmailConfigService()
    config = svc.get()
    if request.method == "POST":
        if not current_user.has_permission("emailtemplate.update"):
            flash("You do not have permission to update email configuration.",
                 "danger")
            return redirect(url_for("system_admin.email_config"))
        svc.update(
            smtp_host=request.form.get("smtp_host") or None,
            smtp_port=int(request.form["smtp_port"]) if request.form.get("smtp_port") else 587,
            smtp_username=request.form.get("smtp_username") or None,
            # Blank password field means "keep the existing one" — never
            # force re-entering a working password just to change another
            # field, and never round-trip the real secret back into the
            # form for display.
            smtp_password=(request.form.get("smtp_password") or config.smtp_password),
            use_tls=request.form.get("use_tls") == "on",
            from_email=request.form.get("from_email") or None,
            from_name=request.form.get("from_name") or None,
            is_enabled=request.form.get("is_enabled") == "on")
        flash("Email configuration updated.", "success")
        return redirect(url_for("system_admin.email_config"))
    return render_template("system_admin/email_config.html", config=config)


@bp.route("/email-config/send-test", methods=["POST"])
@login_required
@require_permission("emailtemplate.update")
def email_config_send_test():
    from app.modules.system_admin.services.email_config_service import (
        EmailSenderService, EmailNotConfiguredError)
    from app.modules.system_admin.tasks import send_test_email

    test_recipient = request.form.get("test_email") or current_user.email
    if not test_recipient:
        flash("Enter a recipient address to send the test to.", "warning")
        return redirect(url_for("system_admin.email_config"))

    # Prefer async delivery so the button returns immediately instead of
    # blocking the request thread on the SMTP handshake. If the broker
    # (Redis) is unavailable — typical in a plain dev run — fall back to a
    # synchronous send. The synchronous send is now timeout-bounded
    # (SMTP_TIMEOUT_SECONDS) so it can never hang the browser indefinitely,
    # which was the original "page just keeps loading" bug.
    try:
        send_test_email.delay(test_recipient)
        flash(f"Test email queued for {test_recipient}. Check the inbox "
              f"(and spam) in a moment; if nothing arrives, review the "
              f"worker log for the SMTP error.", "info")
        return redirect(url_for("system_admin.email_config"))
    except Exception:
        current_app.logger.info(
            "Celery broker unavailable — sending test email synchronously.")

    try:
        EmailSenderService().send(
            to_email=test_recipient,
            subject="Fleet Management System — Test Email",
            body_html=(
                "<p>This is a test email confirming your SMTP configuration "
                "is working correctly.</p>"
                f"<p>Delivery target: {test_recipient}</p>"),
            body_text="This is a test email confirming your SMTP "
                      "configuration is working correctly.")
        flash(f"Test email sent successfully to {test_recipient}.", "success")
    except EmailNotConfiguredError as e:
        flash(str(e), "warning")
    except Exception as e:
        current_app.logger.exception("Test email failed to send: %s", e)
        flash(f"Test email failed to send: {e}", "danger")
    return redirect(url_for("system_admin.email_config"))


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


@bp.route("/email-templates/<int:tmpl_id>/send-test", methods=["POST"])
@login_required
@require_permission("emailtemplate.update")
def email_template_send_test(tmpl_id):
    """Render whatever is currently in the Subject/Body fields — including
    unsaved edits — with realistic sample data, and send it to an address
    the admin types in. Same "preview by actually receiving it" pattern as
    Email Configuration's Send Test Email, but reads from the submitted
    form so a change doesn't have to be saved first just to see how it
    looks rendered by a real mail client."""
    from jinja2 import Template
    from app.modules.system_admin.services.email_config_service import (
        EmailSenderService, EmailNotConfiguredError)

    item = db.session.get(EmailTemplate, tmpl_id)
    if item is None:
        flash("Template not found.", "warning")
        return redirect(url_for("system_admin.email_template_list"))

    test_recipient = request.form.get("test_email") or current_user.email
    if not test_recipient:
        flash("Enter a recipient address to send the test to.", "warning")
        return redirect(url_for("system_admin.email_template_edit",
                                tmpl_id=tmpl_id))

    subject_src = request.form.get("subject") or item.subject
    body_html_src = request.form.get("body_html") or item.body_html or ""
    body_text_src = request.form.get("body_text") or item.body_text or ""
    event_code = request.form.get("event_code") or item.event_code

    # Same fields a real notification would have, but with sample values
    # so the admin can see realistic formatting without an actual pending
    # document.
    sample_context = {
        "recipient_name": current_user.full_name
                         if hasattr(current_user, "full_name")
                         else current_user.username,
        "reference_table": "maintenance_orders",
        "reference_id": 11,
        "document_number": "MO-2026-000011",
        "view_url": url_for("main.dashboard"),
        "event_code": event_code,
        "event_label": event_code.replace("_", " ").title(),
        "comment_body": "This is a sample comment shown so you can see "
                        "how a real comment mention will look in this "
                        "template.",
        "author_name": "Sample User",
    }

    try:
        subject = f"[TEST] {Template(subject_src).render(**sample_context)}"
        body_html = Template(body_html_src).render(**sample_context)
        body_text = Template(body_text_src).render(**sample_context)
        EmailSenderService().send(to_email=test_recipient, subject=subject,
                                  body_html=body_html, body_text=body_text)
        flash(f"Test email sent to {test_recipient} using sample data — "
              f"check that inbox to review formatting.", "success")
    except EmailNotConfiguredError as e:
        flash(str(e), "warning")
    except Exception as e:
        current_app.logger.exception("Template test email failed: %s", e)
        flash(f"Test email failed to send: {e}", "danger")
    return redirect(url_for("system_admin.email_template_edit",
                            tmpl_id=tmpl_id))


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
    from app.modules.system_admin.services.backup_service import BackupService
    from app.modules.system_admin.services.system_parameter_service import (
        SystemParameterService)
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

        # mysqldump path lives in System Parameters (BACKUP group) rather
        # than a BackupConfig column, matching the app-wide "configurable
        # via System Parameters" convention. Upsert so this still works on
        # an install that hasn't re-run `flask seed all` since this field
        # was added.
        mysqldump_path = request.form.get("mysqldump_path", "").strip()
        param = SystemParameter.query.filter_by(code="MYSQLDUMP_PATH").first()
        if param is None:
            param = SystemParameter(code="MYSQLDUMP_PATH", value="",
                                    data_type="STRING", group_name="BACKUP",
                                    description="Full path to mysqldump.exe. "
                                    "Leave blank to auto-detect.")
            db.session.add(param)
        param.value = mysqldump_path

        db.session.commit()
        flash("Backup configuration saved.", "success")
        return redirect(url_for("system_admin.backup_config"))
    backups = BackupService().list_backups(
        cfg.destination_path if cfg else None)
    mysqldump_path = SystemParameterService().get("MYSQLDUMP_PATH", "") or ""
    return render_template("system_admin/backup_config.html", cfg=cfg,
                           backups=backups, mysqldump_path=mysqldump_path)


@bp.route("/backup-config/test", methods=["POST"])
@login_required
@require_permission("backupconfig.update")
def backup_config_test():
    """Non-destructive readiness check — confirms the backup will actually
    work (DB reachable, mysqldump present, destination writable) before the
    client relies on it."""
    from app.modules.system_admin.services.backup_service import BackupService
    dest = request.form.get("destination_path", "")
    mysqldump_path = request.form.get("mysqldump_path", "").strip() or None
    results = BackupService().test_configuration(dest, mysqldump_path)
    for r in results:
        flash(f"{'✓' if r['ok'] else '✗'} {r['check']}: {r['detail']}",
              "success" if r["ok"] else "danger")
    return redirect(url_for("system_admin.backup_config"))


@bp.route("/backup-config/run", methods=["POST"])
@login_required
@require_permission("backupconfig.update")
def backup_config_run():
    """Trigger a backup immediately (manual run). For scheduled runs this
    same service method is invoked by the Celery Beat task."""
    from app.modules.system_admin.services.backup_service import BackupService
    cfg = BackupConfig.query.filter_by(is_active=True).first()
    result = BackupService().run_backup(
        destination_path=cfg.destination_path if cfg else None,
        retention_days=cfg.retention_days if cfg else 30)
    if result["ok"]:
        kb = result["size_bytes"] / 1024
        flash(f"Backup created: {os.path.basename(result['file'])} "
              f"({kb:.1f} KB).", "success")
    else:
        flash(f"Backup failed: {result['error']}", "danger")
    return redirect(url_for("system_admin.backup_config"))


# ── Report Config ──────────────────────────────────────────────────────────

@bp.route("/report-config")
@login_required
@require_permission("reportconfig.view")
def report_config_list():
    from app.modules.system_admin.services.report_registry_service import (
        ReportRegistryService)
    items = ReportConfig.query.order_by(ReportConfig.report_code).all()
    available = ReportRegistryService().list_available(current_user)
    return render_template("system_admin/report_config_list.html",
                           items=items, available=available)


@bp.route("/report-config/new", methods=["GET", "POST"])
@login_required
@require_permission("reportconfig.update")
def report_config_new():
    """Register a new report definition — lets an admin add a report to the
    catalog without a code change (the definition drives the Reports menu)."""
    if request.method == "POST":
        code = request.form["report_code"].strip().upper()
        if ReportConfig.query.filter_by(report_code=code).first():
            flash(f"Report code '{code}' already exists.", "warning")
        else:
            db.session.add(ReportConfig(
                report_code=code,
                name=request.form["name"].strip(),
                description=request.form.get("description", "").strip(),
                template_path=request.form.get("template_path", "").strip()))
            db.session.commit()
            flash(f"Report '{code}' registered.", "success")
        return redirect(url_for("system_admin.report_config_list"))
    return render_template("system_admin/report_config_form.html")


# ── Analytical Reports (Phase 5) ────────────────────────────────────────────

def _report_filters_from_request():
    """Shared filter parsing for the three analytical reports below —
    branch_id / date_from / date_to / status, all optional."""
    return {
        "branch_id": request.args.get("branch_id") or None,
        "date_from": request.args.get("date_from") or None,
        "date_to": request.args.get("date_to") or None,
        "status": request.args.get("status") or None,
    }


def _branch_choices():
    from app.modules.master_data.org.models import Branch
    return Branch.query.filter_by(is_active=True).order_by(Branch.name).all()


@bp.route("/reports/pms-compliance")
@login_required
@require_permission("maintenanceorder.view")
def report_pms_compliance():
    from app.core.maintenance.due_calculation_service import (
        PMDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    filters = _report_filters_from_request()
    scope_svc = UserOrgScopeService()
    rows = [r for r in PMDueCalculationService().get_all_due_vehicles()
           if scope_svc.covers(current_user.id, branch_id=r["vehicle"].branch_id)]
    if filters["branch_id"]:
        rows = [r for r in rows
               if r["vehicle"].branch_id == int(filters["branch_id"])]
    if filters["status"]:
        rows = [r for r in rows if r["status"] == filters["status"]]
    return render_template("system_admin/report_pms_compliance.html",
                           rows=rows, filters=filters,
                           branches=_branch_choices(),
                           generated_at=datetime.now())


@bp.route("/reports/pms-compliance/export.xlsx")
@login_required
@require_permission("maintenanceorder.view")
def report_pms_compliance_export():
    from flask import send_file
    from io import BytesIO
    from app.core.reporting.generators import generate_pms_compliance_xlsx
    filename, data = generate_pms_compliance_xlsx(
        _report_filters_from_request(), user=current_user)
    return send_file(BytesIO(data), as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@bp.route("/reports/registration-expiry")
@login_required
@require_permission("vehicleregistration.view")
def report_registration_expiry():
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    filters = _report_filters_from_request()
    scope_svc = UserOrgScopeService()
    rows = [r for r in RegistrationDueCalculationService().get_all_due_vehicles()
           if scope_svc.covers(current_user.id, branch_id=r["vehicle"].branch_id)]
    if filters["branch_id"]:
        rows = [r for r in rows
               if r["vehicle"].branch_id == int(filters["branch_id"])]
    if filters["status"]:
        rows = [r for r in rows if r["status"] == filters["status"]]
    return render_template("system_admin/report_registration_expiry.html",
                           rows=rows, filters=filters,
                           branches=_branch_choices(),
                           generated_at=datetime.now())


@bp.route("/reports/registration-expiry/export.xlsx")
@login_required
@require_permission("vehicleregistration.view")
def report_registration_expiry_export():
    from flask import send_file
    from io import BytesIO
    from app.core.reporting.generators import generate_registration_expiry_xlsx
    filename, data = generate_registration_expiry_xlsx(
        _report_filters_from_request(), user=current_user)
    return send_file(BytesIO(data), as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


@bp.route("/reports/maintenance-cost-summary")
@login_required
@require_permission("maintenanceorder.view")
def report_maintenance_cost_summary():
    from app.extensions import db
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    filters = _report_filters_from_request()

    query = MaintenanceOrder.query.filter_by(status="COMPLETED")
    if filters["branch_id"]:
        query = query.filter(MaintenanceOrder.vehicle.has(
            branch_id=int(filters["branch_id"])))
    if filters["date_from"]:
        query = query.filter(MaintenanceOrder.completed_date >= filters["date_from"])
    if filters["date_to"]:
        query = query.filter(MaintenanceOrder.completed_date <= filters["date_to"])
    orders = query.order_by(MaintenanceOrder.completed_date.desc()).all()

    scope_svc = UserOrgScopeService()
    orders = [o for o in orders
             if scope_svc.covers(current_user.id, branch_id=o.vehicle.branch_id)]
    total = sum(float(o.actual_cost or 0) for o in orders)

    return render_template("system_admin/report_maintenance_cost_summary.html",
                           orders=orders, total=total, filters=filters,
                           branches=_branch_choices(),
                           generated_at=datetime.now())


@bp.route("/reports/maintenance-cost-summary/export.xlsx")
@login_required
@require_permission("maintenanceorder.view")
def report_maintenance_cost_summary_export():
    from flask import send_file
    from io import BytesIO
    from app.core.reporting.generators import (
        generate_maintenance_cost_summary_xlsx)
    filename, data = generate_maintenance_cost_summary_xlsx(
        _report_filters_from_request(), user=current_user)
    return send_file(BytesIO(data), as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument"
                              ".spreadsheetml.sheet")


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
