"""System Administration models for Phase 1c."""
from datetime import datetime, timezone

from app.extensions import db
from app.core.models.base import BaseModel


class SystemParameter(db.Model, BaseModel):
    __tablename__ = "system_parameters"
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False, default="")
    # STRING | INTEGER | BOOLEAN | DECIMAL
    data_type = db.Column(db.String(10), nullable=False, default="STRING")
    description = db.Column(db.String(255))
    group_name = db.Column(db.String(80), nullable=False, default="GENERAL",
                           index=True)
    is_editable = db.Column(db.Boolean, default=True, nullable=False)


class Lookup(db.Model, BaseModel):
    __tablename__ = "lookups"
    lookup_type = db.Column(db.String(80), nullable=False, index=True)
    code = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("lookup_type", "code", name="uq_lookup_type_code"),
    )


class CompanyProfile(db.Model, BaseModel):
    __tablename__ = "company_profiles"
    company_name = db.Column(db.String(200), nullable=False)
    address_line1 = db.Column(db.String(255))
    address_line2 = db.Column(db.String(255))
    city = db.Column(db.String(100))
    country = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    tin = db.Column(db.String(50))
    logo_filename = db.Column(db.String(255))


class EmailConfig(db.Model, BaseModel):
    """SMTP delivery settings — singleton row, same pattern as
    CompanyProfile. Distinct from EmailTemplate (content) — this is the
    transport configuration that actually sends mail."""
    __tablename__ = "email_config"
    smtp_host = db.Column(db.String(255), nullable=True)
    smtp_port = db.Column(db.Integer, nullable=True, default=587)
    smtp_username = db.Column(db.String(255), nullable=True)
    smtp_password = db.Column(db.String(255), nullable=True)
    use_tls = db.Column(db.Boolean, default=True, nullable=False)
    from_email = db.Column(db.String(255), nullable=True)
    from_name = db.Column(db.String(120), nullable=True,
                          default="Fleet Management System")
    is_enabled = db.Column(db.Boolean, default=False, nullable=False)


class EmailTemplate(db.Model, BaseModel):
    __tablename__ = "email_templates"
    event_code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    body_html = db.Column(db.Text, nullable=False, default="")
    body_text = db.Column(db.Text, nullable=False, default="")


class NotificationRule(db.Model, BaseModel):
    __tablename__ = "notification_rules"
    event_code = db.Column(db.String(80), nullable=False, index=True)
    # IN_APP | EMAIL | BOTH
    channel = db.Column(db.String(10), nullable=False, default="IN_APP")
    # SUBMITTER | CURRENT_APPROVER | ROLE | SPECIFIC_USER
    recipient_type = db.Column(db.String(20), nullable=False)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    role = db.relationship("Role")
    user = db.relationship("User")


class InAppNotification(db.Model, BaseModel):
    __tablename__ = "in_app_notifications"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False,
                        index=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    event_code = db.Column(db.String(80), nullable=True)
    reference_table = db.Column(db.String(100), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    is_read = db.Column(db.Boolean, default=False, nullable=False, index=True)
    read_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")


class DashboardWidget(db.Model, BaseModel):
    __tablename__ = "dashboard_widgets"
    code = db.Column(db.String(80), unique=True, nullable=False)
    label = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(50), nullable=False, default="bi-grid")
    default_visible = db.Column(db.Boolean, default=True, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class UserDashboardConfig(db.Model, BaseModel):
    __tablename__ = "user_dashboard_configs"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    widget_code = db.Column(db.String(80), nullable=False)
    is_visible = db.Column(db.Boolean, default=True, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("user_id", "widget_code", name="uq_user_widget"),
    )


class BackupConfig(db.Model, BaseModel):
    __tablename__ = "backup_configs"
    # DAILY | WEEKLY | MANUAL
    schedule = db.Column(db.String(10), nullable=False, default="MANUAL")
    retention_days = db.Column(db.Integer, default=30, nullable=False)
    destination_path = db.Column(db.String(500))
    is_active = db.Column(db.Boolean, default=True, nullable=False)


class ReportConfig(db.Model, BaseModel):
    __tablename__ = "report_configs"
    report_code = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    template_path = db.Column(db.String(500))


class ScheduledReport(db.Model, BaseModel):
    """Recurring, unattended email delivery of any report registered in
    app.core.reporting.generators.REPORT_GENERATORS. Triggered by the
    `flask report run-due` CLI command (see app/cli.py) rather than a
    Celery Beat schedule -- matches this project's existing pattern for
    recurring jobs (flask pm run-due-check, flask registration
    run-due-check), so no separate `celery beat` process is required;
    whatever already triggers those two (OS cron / Windows Task
    Scheduler) can trigger this one too, e.g. hourly. Idempotent and
    safe to run as often as you like — it only sends when a schedule's
    computed next_run_at has passed."""
    __tablename__ = "scheduled_reports"

    name = db.Column(db.String(150), nullable=False)
    report_code = db.Column(db.String(60), nullable=False)
    frequency = db.Column(db.String(10), nullable=False, default="WEEKLY")
    # DAILY | WEEKLY | MONTHLY

    # Comma-separated email addresses -- kept simple (no join table)
    # since recipients here are delivery addresses, not app Users who
    # need permission checks; anyone can be an external recipient (e.g.
    # a branch manager who isn't an FMS user at all).
    recipients = db.Column(db.Text, nullable=False)

    # JSON-encoded filters dict matching the generator's `filters` param
    # (branch_id / status; date_from/date_to are intentionally NOT
    # supported here in v1 -- see generators.py docstring -- so every
    # scheduled run reflects the live, as-of-now snapshot).
    filters_json = db.Column(db.Text, nullable=True)

    last_run_at = db.Column(db.DateTime, nullable=True)
    next_run_at = db.Column(db.DateTime, nullable=True)
    last_run_status = db.Column(db.String(20), nullable=True)
    # SUCCESS | FAILED, set after each run for the admin to see at a glance

    def recipient_list(self) -> list:
        return [r.strip() for r in (self.recipients or "").split(",")
               if r.strip()]

    def filters_dict(self) -> dict:
        import json
        if not self.filters_json:
            return {}
        try:
            return json.loads(self.filters_json)
        except (ValueError, TypeError):
            return {}

    def compute_next_run(self, from_time=None):
        from datetime import datetime, timezone, timedelta
        base = from_time or datetime.now(timezone.utc)
        if self.frequency == "DAILY":
            return base + timedelta(days=1)
        if self.frequency == "MONTHLY":
            return base + timedelta(days=30)
        return base + timedelta(days=7)  # WEEKLY, default


class EmailOutbox(db.Model, BaseModel):
    """Outbound email queue.

    Notification emails used to be sent SYNCHRONOUSLY inside the web
    request -- one SMTP connection per recipient, over implicit SSL to a
    remote host. With several recipients that pushed a simple "Submit"
    to roughly a minute before the browser saw any confirmation, because
    the request could not return until every message had been handed to
    the mail server.

    Writing a row here instead takes microseconds, so the UI responds
    immediately; a scheduled drain (`flask email send-pending`, or the
    Celery beat task) delivers them out-of-band and flips each row to
    SENT. A row that fails records the error and its attempt count and is
    retried on the next run, so a transient SMTP outage delays mail
    rather than losing it -- which the old fire-and-forget path could do
    silently.
    """
    __tablename__ = "email_outbox"

    to_email = db.Column(db.String(255), nullable=False, index=True)
    to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                           nullable=True)
    subject = db.Column(db.String(255), nullable=False)
    body_html = db.Column(db.Text, nullable=True)
    # Kept so the drain can re-render from live data if the body was not
    # pre-rendered, and so the outbox screen can link back to the source.
    event_code = db.Column(db.String(60), nullable=True, index=True)
    reference_table = db.Column(db.String(100), nullable=True)
    reference_id = db.Column(db.Integer, nullable=True)
    comment_id = db.Column(db.Integer, nullable=True)
    # PENDING | SENT | FAILED
    status = db.Column(db.String(10), default="PENDING", nullable=False,
                       index=True)
    attempts = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.Text, nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)


class CustomReport(db.Model, BaseModel):
    """A user-built report definition from the Report Builder.

    Stores only the CHOICES a person made (which data source, which
    column keys, which filters), never SQL. The query is rebuilt from
    those keys by report_builder.run_report() every time it runs, and
    every key is validated against the curated whitelist at run time --
    so a saved definition can't outlive a column being removed, and a
    row edited directly in the database still can't smuggle in SQL or
    reach a table the builder doesn't expose.
    """
    __tablename__ = "custom_reports"

    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    # Key into report_builder.DATA_SOURCES
    data_source = db.Column(db.String(60), nullable=False)
    # JSON: ["plate_number", "brand", ...]
    fields_json = db.Column(db.Text, nullable=False, default="[]")
    # JSON: [{"field": "...", "op": "eq", "value": "..."}, ...]
    filters_json = db.Column(db.Text, nullable=True)
    sort_key = db.Column(db.String(60), nullable=True)
    sort_dir = db.Column(db.String(4), default="asc", nullable=False)
    row_limit = db.Column(db.Integer, default=1000, nullable=False)
    # Optional default recipients for on-demand "email me this" sends.
    default_recipients = db.Column(db.Text, nullable=True)

    @property
    def fields(self) -> list:
        import json
        try:
            return json.loads(self.fields_json or "[]")
        except ValueError:
            return []

    @property
    def filters(self) -> list:
        import json
        try:
            return json.loads(self.filters_json or "[]")
        except ValueError:
            return []
