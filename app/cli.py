"""Seed commands: `flask seed permissions|admin|all`."""
import click
from flask.cli import AppGroup

from app.extensions import db
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.user_management.models import User, Role, Permission

seed_cli = AppGroup("seed", help="Seed initial data.")


@seed_cli.command("permissions")
def seed_permissions():
    """Sync code-registered permissions into the database."""
    sync_permissions()
    db.session.commit()
    click.echo(f"Permissions synced: {Permission.query.count()} total.")


@seed_cli.command("admin")
@click.option("--admin-password", prompt=True, hide_input=True,
              help="Password for the admin account.")
def seed_admin(admin_password):
    """Create the System Administrator role (all permissions) and admin user."""
    _seed_admin(admin_password)


@seed_cli.command("all")
@click.option("--admin-password", prompt=True, hide_input=True)
def seed_all(admin_password):
    """Sync permissions then create the admin role/user and seed defaults."""
    sync_permissions()
    db.session.commit()
    _seed_admin(admin_password)
    _seed_system_parameters()
    _seed_dashboard_widgets()
    _seed_lookups()
    _seed_transaction_types()
    _seed_email_templates()
    _seed_notification_rules()
    _seed_reports()
    _seed_atr_numbering()
    _migrate_report_permissions_from_data_permissions()
    db.session.commit()
    click.echo("Default system parameters, dashboard widgets, lookups, "
               "email templates and notification rules seeded.")


def _migrate_report_permissions_from_data_permissions() -> None:
    """One-time upgrade step: reports used to be gated on the underlying
    data's own view permission (vehicle.view, maintenanceorder.view,
    vehicleregistration.view). Now each report has its own dedicated
    permission so an admin can grant/revoke access to a SPECIFIC report
    per Role independently. This grants the new report permission to any
    role that already had the old coupled data permission, ONCE, so
    nobody loses access on upgrade.

    Genuinely one-time, not just idempotent: `flask seed all` runs
    repeatedly over the life of the app (every deploy), and this
    function is called from it every time. If it re-checked the old
    coupling on every run, an admin who deliberately unchecked one of
    these report permissions for a role would find it silently
    re-granted the next time someone ran `flask seed all` -- defeating
    the entire point of the per-report toggle. A dedicated SystemParameter
    row marks the migration as done so it only ever runs once. (Not
    using SystemParameterService.set() here since it only updates an
    already-existing parameter and would silently no-op for a brand-new
    code like this one.)"""
    from app.modules.system_admin.models import SystemParameter
    from app.modules.user_management.models import Role, Permission

    marker_code = "REPORT_PERMISSIONS_MIGRATED_V1"
    marker = SystemParameter.query.filter_by(code=marker_code).first()
    if marker is not None and marker.value == "true":
        return

    coupling = [
        ("vehicle.view", "reportvehicleregister.view"),
        ("maintenanceorder.view", "reportpmscompliance.view"),
        ("vehicleregistration.view", "reportregistrationexpiry.view"),
        ("maintenanceorder.view", "reportmaintenancecost.view"),
    ]
    for old_code, new_code in coupling:
        old_perm = Permission.query.filter_by(code=old_code).first()
        new_perm = Permission.query.filter_by(code=new_code).first()
        if old_perm is None or new_perm is None:
            continue
        for role in old_perm.roles:
            if new_perm not in role.permissions:
                role.permissions.append(new_perm)

    if marker is None:
        db.session.add(SystemParameter(
            code=marker_code, value="true", data_type="STRING",
            group_name="INTERNAL", is_editable=False,
            description="Internal one-time migration marker — do not edit."))
    else:
        marker.value = "true"


def _seed_admin(admin_password: str) -> None:
    role = Role.query.filter_by(name="System Administrator").first()
    if role is None:
        role = Role(name="System Administrator",
                    description="Full access to all modules",
                    is_system_role=True)
        db.session.add(role)
    role.permissions = Permission.query.all()

    admin = User.query.filter_by(username="admin").first()
    if admin is None:
        admin = User(username="admin", email="admin@example.com",
                     password_hash=hash_password(admin_password),
                     first_name="System", last_name="Administrator",
                     must_change_password=True)
        admin.roles.append(role)
        db.session.add(admin)
        click.echo("Admin user created (username: admin).")
    else:
        click.echo("Admin user already exists; skipped.")
    db.session.commit()


def _seed_system_parameters() -> None:
    """Seed the configurable business rules. Grouped to mirror the legacy
    VEMS 'Vehicle Management System Config' screen (System Configuration
    tab) so the client sees familiar settings, plus the FMS-specific
    security/trip-ticket params. Idempotent: only inserts codes that don't
    already exist, so it's safe to re-run after adding new defaults."""
    from app.modules.system_admin.models import SystemParameter
    defaults = [
        # (code, value, data_type, group, description)

        # ── Security / session (FMS) ──────────────────────────────────
        ("SESSION_TIMEOUT_MINUTES", "30", "INTEGER", "SECURITY",
         "Session timeout in minutes"),
        ("MAX_FAILED_LOGIN_ATTEMPTS", "5", "INTEGER", "SECURITY",
         "Max failed login attempts before lockout"),

        # ── Password policy (VEMS: System Configuration) ──────────────
        ("PASSWORD_WARNING_DAYS", "30", "INTEGER", "PASSWORD_POLICY",
         "Days before expiry to start warning the user"),
        ("PASSWORD_EXPIRY_DAYS", "90", "INTEGER", "PASSWORD_POLICY",
         "Password validity period in days"),
        ("PASSWORD_HISTORY_LENGTH", "6", "INTEGER", "PASSWORD_POLICY",
         "Number of previous passwords that cannot be reused"),
        ("PASSWORD_MIN_LENGTH", "6", "INTEGER", "PASSWORD_POLICY",
         "Minimum password length"),
        ("PASSWORD_MAX_LENGTH", "20", "INTEGER", "PASSWORD_POLICY",
         "Maximum password length"),

        # ── Image size limits, px (VEMS: Vehicle Picture/Person Setup) ─
        ("IMG_VEHICLE_FRONT_BACK_PX", "400", "INTEGER", "IMAGE_LIMITS",
         "Vehicle picture size in pixels (Front, Back)"),
        ("IMG_CR_PX", "600", "INTEGER", "IMAGE_LIMITS",
         "CR picture size in pixels"),
        ("IMG_PERSON_ATD_PX", "320", "INTEGER", "IMAGE_LIMITS",
         "Person/Assignee/ATD picture size in pixels"),
        ("IMG_LTO_ENGINE_PX", "600", "INTEGER", "IMAGE_LIMITS",
         "LTO/Engine picture size in pixels"),
        ("IMG_ENGINE_CR_PX", "800", "INTEGER", "IMAGE_LIMITS",
         "Engine picture size in pixels (CR)"),

        # ── Car plan budget over 5 years (VEMS) ───────────────────────
        ("CAR_PLAN_BUDGET_Y1", "3000", "DECIMAL", "CAR_PLAN_BUDGET",
         "Car Plan budget, year 1"),
        ("CAR_PLAN_BUDGET_Y2", "3750", "DECIMAL", "CAR_PLAN_BUDGET",
         "Car Plan budget, year 2"),
        ("CAR_PLAN_BUDGET_Y3", "4500", "DECIMAL", "CAR_PLAN_BUDGET",
         "Car Plan budget, year 3"),
        ("CAR_PLAN_BUDGET_Y4", "5250", "DECIMAL", "CAR_PLAN_BUDGET",
         "Car Plan budget, year 4"),
        ("CAR_PLAN_BUDGET_Y5", "6000", "DECIMAL", "CAR_PLAN_BUDGET",
         "Car Plan budget, year 5"),

        # ── Company-owned budget over 5 years (VEMS) ──────────────────
        ("COMPANY_OWNED_BUDGET_Y1", "2000", "DECIMAL", "COMPANY_OWNED_BUDGET",
         "Company-owned vehicle budget, year 1"),
        ("COMPANY_OWNED_BUDGET_Y2", "2500", "DECIMAL", "COMPANY_OWNED_BUDGET",
         "Company-owned vehicle budget, year 2"),
        ("COMPANY_OWNED_BUDGET_Y3", "3000", "DECIMAL", "COMPANY_OWNED_BUDGET",
         "Company-owned vehicle budget, year 3"),
        ("COMPANY_OWNED_BUDGET_Y4", "3500", "DECIMAL", "COMPANY_OWNED_BUDGET",
         "Company-owned vehicle budget, year 4"),
        ("COMPANY_OWNED_BUDGET_Y5", "4000", "DECIMAL", "COMPANY_OWNED_BUDGET",
         "Company-owned vehicle budget, year 5"),
        ("BUDGET_TRACKING_MODE", "PER_YEAR", "STRING", "CAR_PLAN_BUDGET",
         "PER_YEAR (each vehicle-year checked only against its own Y-tier "
         "budget, no carryover) or ACCUMULATED (Y1..current tiers summed "
         "into one lifetime pool vs total spend to date)."),

        # ── Finance (VEMS: Other Setting) — read by Vehicle Registration
        ("VAT_RATE", "12", "DECIMAL", "FINANCE",
         "VAT rate (%) applied on registration/computed amounts"),
        ("ASSURED_VALUE_PCT", "10", "DECIMAL", "FINANCE",
         "% Assured Value used in vehicle registration computation"),

        # ── UI list preferences (VEMS: Other Setting) ─────────────────
        ("LIST_PAGES", "75", "INTEGER", "UI_PREFERENCES",
         "Default number of rows per list page"),
        ("VEHICLE_LIST_COLUMNS", "38", "INTEGER", "UI_PREFERENCES",
         "Vehicle list column count"),
        ("WO_NORMAL_LIST_COLUMNS", "15", "INTEGER", "UI_PREFERENCES",
         "Work Order (Normal) list column count"),
        ("WO_PM_LIST_COLUMNS", "15", "INTEGER", "UI_PREFERENCES",
         "Work Order (PM) list column count"),

        # ── Backup / restore ───────────────────────────────────────────
        ("MYSQLDUMP_PATH", "", "STRING", "BACKUP",
         "Full path to mysqldump.exe. Leave blank to auto-detect from PATH "
         "or common MySQL install locations."),

        # ── Trip ticket / general (FMS) ───────────────────────────────
        ("REQUIRE_DRIVER_FROM_MASTER", "YES", "STRING", "TRIP_TICKET",
         "YES = driver must come from Driver Master; NO = manual entry"),
        ("COMPANY_NAME", "My Company", "STRING", "GENERAL", "Company name"),
    ]
    for code, value, data_type, group, desc in defaults:
        if not SystemParameter.query.filter_by(code=code).first():
            db.session.add(SystemParameter(
                code=code, value=value, data_type=data_type,
                group_name=group, description=desc))
    db.session.flush()


def _seed_dashboard_widgets() -> None:
    """Seed the dashboard widget catalog. Two kinds of widget:
      - KPI tiles (top row counters): FLEET, MAINTENANCE, APPROVALS, ...
      - Panels (list/table sections): MY_ACTIONS, VEHICLE_LIST,
        DUE_MAINTENANCE, DUE_REGISTRATION.
    Every widget is individually toggleable per user under System
    Administration → Dashboard Config, so a CEO and a dispatcher can see
    different dashboards."""
    from app.modules.system_admin.models import DashboardWidget
    widgets = [
        # KPI tiles
        ("FLEET", "Fleet", "bi-truck", 1),
        ("MAINTENANCE", "Maintenance", "bi-wrench", 2),
        ("APPROVALS", "Approvals", "bi-check2-square", 3),
        ("REGISTRATIONS", "Registrations", "bi-card-checklist", 4),
        ("TIRES", "Tires", "bi-circle", 5),
        ("BATTERIES", "Batteries", "bi-battery-half", 6),
        # Panels (list/table widgets)
        ("MY_ACTIONS", "For My Action", "bi-inbox", 10),
        ("VEHICLE_LIST", "Vehicle List", "bi-list-ul", 11),
        ("DUE_MAINTENANCE", "Vehicles Due for Maintenance", "bi-wrench", 12),
        ("DUE_REGISTRATION", "Vehicles Due for Registration", "bi-card-checklist", 13),
    ]
    for code, label, icon, sort in widgets:
        if not DashboardWidget.query.filter_by(code=code).first():
            db.session.add(DashboardWidget(
                code=code, label=label, icon=icon,
                sort_order=sort, default_visible=True))
    db.session.flush()


def _seed_email_templates() -> None:
    """Seed default Jinja2 email templates for every approval-engine event
    and the scheduled reminder events. Admins can edit subject/body under
    System Administration -> Email Templates without touching code.

    Available context variables (see notification tasks.py ->
    _build_notification_context):
      {{ recipient_name }}   - the person being emailed
      {{ document_number }} - the REAL document number (e.g.
                              "MO-2026-000011"), not the raw database id
      {{ reference_table }}, {{ reference_id }} - the raw generic key,
                              still available for custom templates that
                              want it, but document_number is what should
                              be shown to a person
      {{ view_url }}        - absolute path back into the app for this
                              document (empty string if not resolvable)
      {{ event_code }}, {{ event_label }}
      {{ comment_body }}, {{ author_name }} - populated only for the
                              DOCUMENT_COMMENT event; empty otherwise
    """
    from app.modules.system_admin.models import EmailTemplate

    def _tmpl(intro, include_comment=False):
        view_link = (
            '<p><a href="{{ view_url }}">Open this document</a></p>'
            if True else "")
        comment_block = (
            '{% if comment_body %}'
            '<blockquote style="margin:0 0 1em 0;padding:0.5em 1em;'
            'border-left:3px solid #ccc;color:#333;">'
            '<strong>{{ author_name }}</strong> wrote:<br>{{ comment_body }}'
            '</blockquote>{% endif %}'
            if include_comment else "")
        html = (f"<p>Hello {{{{ recipient_name }}}},</p>"
                f"<p>{intro}</p>"
                f"{comment_block}"
                f"<p><strong>{{{{ document_number }}}}</strong></p>"
                f'{view_link}')
        comment_text = (
            "{% if comment_body %}\n\"{{ author_name }} wrote: "
            "{{ comment_body }}\"\n\n{% endif %}" if include_comment else "")
        text = (f"Hello {{{{ recipient_name }}}},\n\n{intro}\n\n"
                f"{comment_text}"
                f"{{{{ document_number }}}}\n\n"
                f"{{% if view_url %}}Open this document: {{{{ view_url }}}}"
                f"{{% endif %}}")
        return html, text

    templates = [
        ("submitted", "Document Submitted for Approval",
         "[FMS] Approval needed: {{ document_number }}",
         "A document has been submitted and is now awaiting your approval.",
         False),
        ("approved_level", "Approval Level Passed",
         "[FMS] Progressed: {{ document_number }}",
         "A document you submitted has passed an approval level and moved "
         "to the next approver.", False),
        ("approved_final", "Document Fully Approved",
         "[FMS] Approved: {{ document_number }}",
         "Good news — your document has been fully approved. You may "
         "proceed with execution.", False),
        ("rejected", "Document Rejected",
         "[FMS] Rejected: {{ document_number }}",
         "Your document has been rejected. Please review the approver's "
         "remarks.", False),
        ("returned", "Document Returned",
         "[FMS] Returned for revision: {{ document_number }}",
         "Your document has been returned for revision.", False),
        ("resubmitted", "Document Resubmitted",
         "[FMS] Resubmitted: {{ document_number }}",
         "A previously returned document has been revised and resubmitted "
         "for your approval.", False),
        ("cancelled", "Document Cancelled",
         "[FMS] Cancelled: {{ document_number }}",
         "A document has been cancelled.", False),
        ("PMS_DUE", "Preventive Maintenance Due",
         "[FMS] PM due: {{ document_number }}",
         "A vehicle is due for preventive maintenance.", False),
        ("REGISTRATION_EXPIRING", "Vehicle Registration Expiring",
         "[FMS] Registration expiring: {{ document_number }}",
         "A vehicle registration is approaching its expiry date and needs "
         "renewal.", False),
        ("TIRE_REPLACEMENT", "Tire Replacement Due",
         "[FMS] Tire replacement due: {{ document_number }}",
         "A tire has reached its replacement threshold.", False),
        ("BATTERY_REPLACEMENT", "Battery Replacement Due",
         "[FMS] Battery replacement due: {{ document_number }}",
         "A battery has reached its replacement threshold.", False),
        ("TRIP_TICKET_RELEASE", "Trip Ticket Released",
         "[FMS] Trip ticket released: {{ document_number }}",
         "A trip ticket has been released.", False),
        ("DOCUMENT_COMMENT", "Mentioned in a Comment",
         "[FMS] You were mentioned: {{ document_number }}",
         "Someone mentioned you in a comment on a document:", True),
    ]
    for event_code, name, subject, intro, include_comment in templates:
        if not EmailTemplate.query.filter_by(event_code=event_code).first():
            html, text = _tmpl(intro, include_comment)
            db.session.add(EmailTemplate(
                event_code=event_code, name=name, subject=subject,
                body_html=html, body_text=text))
    db.session.flush()


def _seed_notification_rules() -> None:
    """Seed default notification rules so approval events actually notify
    the right people out of the box. Channel defaults to BOTH (in-app +
    email); admins can switch any rule to IN_APP only under System
    Administration → Notification Rules.

    Recipient logic:
      - submitted/resubmitted → CURRENT_APPROVER (the person who must act)
      - approved_final/rejected/returned → SUBMITTER (the originator)
    """
    from app.modules.system_admin.models import NotificationRule

    rules = [
        ("submitted", "BOTH", "CURRENT_APPROVER"),
        ("resubmitted", "BOTH", "CURRENT_APPROVER"),
        ("approved_level", "BOTH", "CURRENT_APPROVER"),
        ("approved_final", "BOTH", "SUBMITTER"),
        ("rejected", "BOTH", "SUBMITTER"),
        ("returned", "BOTH", "SUBMITTER"),
        ("cancelled", "IN_APP", "SUBMITTER"),
    ]
    for event_code, channel, recipient_type in rules:
        exists = NotificationRule.query.filter_by(
            event_code=event_code, recipient_type=recipient_type).first()
        if not exists:
            db.session.add(NotificationRule(
                event_code=event_code, channel=channel,
                recipient_type=recipient_type))
    db.session.flush()


def _seed_reports() -> None:
    """Seed the built-in report registry so the 5 shipped reports appear in
    the unified Reports list; admins add new reports as ReportConfig rows."""
    from app.modules.system_admin.services.report_registry_service import (
        ReportRegistryService)
    ReportRegistryService().seed_builtin()


def _seed_atr_numbering() -> None:
    """Seed the ATR (Asset Transfer Report) and ADR (Asset Disposal
    Report) document types + numbering schemes -- ATR-2026-0001 /
    ADR-2026-0001 style -- so a branch-to-branch vehicle transfer or a
    vehicle retirement/disposal, both driven off an Operational MO, has
    its own printable reference number. Same pattern as ATD/PR/TT each
    having their own series. Every other DocumentType/NumberingScheme in
    this app is configured manually via System Administration -> Document
    Type Maintenance, but these two are seeded here so the Asset Transfer
    Report and Asset Disposal Report print out of the box without
    requiring that manual setup step first."""
    from app.modules.document_config.models import DocumentType, NumberingScheme

    for code, name, description in [
        ("ATR", "Asset Transfer Report",
         "Branch-to-branch vehicle transfer reference, generated from a "
         "Relocation/Transfer Maintenance Order."),
        ("ADR", "Asset Disposal Report",
         "Vehicle retirement/disposal reference, generated from a "
         "Disposal-group Maintenance Order."),
    ]:
        dt = DocumentType.query.filter_by(code=code).first()
        if dt is None:
            dt = DocumentType(code=code, name=name, requires_approval=False,
                              auto_numbering=True, printable=True,
                              mobile_available=False, attachment_allowed=False,
                              description=description)
            db.session.add(dt)
            db.session.flush()
        if dt.numbering_scheme is None:
            db.session.add(NumberingScheme(
                document_type_id=dt.id, prefix=code, include_year=True,
                include_month=False, digit_count=4, separator="-",
                reset_policy="YEARLY"))


def _seed_lookups() -> None:
    """Sync all module-registered lookup types (FUEL_TYPE, LICENSE_TYPE, etc.)
    Must import the master_data routes module first so its lookup
    registrations run (module-level registry.register() calls)."""
    import app.modules.master_data.routes  # noqa: F401 (triggers registration)
    from app.modules.system_admin.services.lookup_service import sync_lookups
    sync_lookups()


def _seed_transaction_types() -> None:
    """Default Transaction Types for the MO Category/Transaction Type
    enhancement — admin-configurable from here on (System Admin can add
    more later), but these are the ones named explicitly in the spec.
    'group' only organizes the New MO form's dropdown into optgroups; the
    real Category (used for validation) is order_category."""
    from app.modules.transactions.maintenance_order.models import TransactionType

    defaults = [
        # (code, name, order_category, group)
        ("MAINT-SERVICING", "Servicing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPAIR", "Repair", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-TROUBLESHOOT", "Troubleshooting", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-TOWING", "Road Call / Towing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-OVERHAUL", "Overhauling", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-INSPECTION", "Inspection", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-EMISSION", "Emission Test", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPL-UNIT", "Replacement Unit", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REPAINT", "Repainting", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REHAB", "Rehabilitation", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-REWIND", "Rewinding", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-FABRICATION", "Fabrication", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-INSTALLATION", "Installation", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-UPGRADE", "Upgrading", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-WASHING", "Washing / Detailing", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ADJUSTMENT", "Adjustment / Alignment", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ACCIDENT-MINOR", "Accident Minor", "MAINTENANCE", "MAINTENANCE"),
        ("MAINT-ACCIDENT-MAJOR", "Accident Major", "MAINTENANCE", "MAINTENANCE"),

        ("DEP-ASSIGNMENT", "Assignment", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-REASSIGNMENT", "Reassignment", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-RELOCATION", "Relocation", "OPERATIONAL", "DEPLOYMENT"),
        ("DEP-TRANSFER", "Transfer / Relocation", "OPERATIONAL", "DEPLOYMENT"),

        ("ADM-REG-AMEND", "Registration Amendments", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-OWNER", "Change of Ownership", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-COLOR", "Change Color", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-ENGINE", "Change Engine", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-CHASSIS", "Change Chassis", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CHG-BODY", "Change Body Type", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-MORTGAGE", "Mortgage / Annotation", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-CANCEL-MORTGAGE", "Cancellation of Mortgage", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-TRAFFIC-VIOLATION", "Traffic Violation / Apprehension", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-TRAINING", "Training / Seminar", "OPERATIONAL", "ADMINISTRATIVE"),
        ("ADM-MIGRATE-EXPENSE", "Migration of Repair Expenses", "OPERATIONAL", "ADMINISTRATIVE"),

        ("DIS-SCRAPPAGE", "Scrappage", "OPERATIONAL", "DISPOSAL"),
        ("DIS-CARNAPPED", "Carnapped", "OPERATIONAL", "DISPOSAL"),
        ("DIS-TOTAL-LOSS", "Total Loss / Wreck", "OPERATIONAL", "DISPOSAL"),
        ("DIS-UNECONOMICAL", "Uneconomical to Repair", "OPERATIONAL", "DISPOSAL"),
        ("DIS-SOLD", "Sold / Auctioned", "OPERATIONAL", "DISPOSAL"),
        ("DIS-DONATED", "Donated", "OPERATIONAL", "DISPOSAL"),

        ("ACC-APPLICATION", "Application", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-INSTALLATION", "Installation", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-FABRICATION", "Fabrication", "OPERATIONAL", "ACCESSORIES"),
        ("ACC-REPLACEMENT", "Replacement", "OPERATIONAL", "ACCESSORIES"),
    ]
    for i, (code, name, order_category, group) in enumerate(defaults):
        if not TransactionType.query.filter_by(code=code).first():
            db.session.add(TransactionType(
                code=code, name=name, order_category=order_category,
                group=group, sort_order=i))
    db.session.commit()


pm_cli = AppGroup("pm", help="Preventive Maintenance testing/ops commands.")


@pm_cli.command("run-due-check")
def pm_run_due_check():
    """Manually run the same due/overdue scan Celery beat would run daily
    — fires notifications and (only for AUTO_MO-policy templates) creates
    Maintenance Orders. Safe to run repeatedly; idempotent."""
    from app.modules.transactions.maintenance_order.tasks import (
        auto_generate_due_maintenance_orders)
    created = auto_generate_due_maintenance_orders()
    click.echo(f"Due/overdue scan complete. Maintenance Orders created: {created}")


registration_cli = AppGroup("registration",
                            help="Vehicle Registration renewal testing/ops commands.")


@registration_cli.command("run-due-check")
def registration_run_due_check():
    """Manually run the same registration-renewal due/overdue scan
    Celery beat would run daily — fires notifications and (only for
    AUTO_REGISTRATION-policy templates) creates DRAFT renewal
    registrations. Safe to run repeatedly; idempotent."""
    from app.modules.transactions.vehicle_registration.tasks import (
        auto_generate_due_registrations)
    created = auto_generate_due_registrations()
    click.echo(f"Due/overdue scan complete. Registrations created: {created}")


report_cli = AppGroup("report", help="Scheduled report delivery commands.")


@report_cli.command("run-due")
def report_run_due():
    """Send every ScheduledReport whose next_run_at has passed, then
    advance it to the next occurrence. Safe to run as often as you like
    (e.g. hourly via cron/Task Scheduler) — a schedule with a future
    next_run_at is simply skipped, so this never double-sends. This is
    the report-delivery equivalent of `flask pm run-due-check` /
    `flask registration run-due-check` — same trigger pattern, no
    separate Celery Beat process required."""
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    sent, failed = ScheduledReportService().run_due()
    click.echo(f"Scheduled report run complete. Sent: {sent}, Failed: {failed}.")


def register_cli(app):
    app.cli.add_command(seed_cli)
    app.cli.add_command(pm_cli)
    app.cli.add_command(registration_cli)
    app.cli.add_command(report_cli)
