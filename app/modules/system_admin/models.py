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
