"""SMTP configuration and delivery — replaces the earlier stubbed-out
send_notification_email task (which only logged intent) with real email
sending, gated behind an admin-configurable enable switch and settings.
"""
import smtplib
from email.message import EmailMessage

from app.extensions import db
from app.modules.system_admin.models import EmailConfig


class EmailNotConfiguredError(Exception):
    pass


class EmailConfigService:
    def get(self) -> EmailConfig:
        config = EmailConfig.query.first()
        if config is None:
            config = EmailConfig()
            db.session.add(config)
            db.session.commit()
        return config

    def update(self, **kwargs) -> EmailConfig:
        config = self.get()
        for k, v in kwargs.items():
            setattr(config, k, v)
        db.session.commit()
        return config


class EmailSenderService:
    def send(self, *, to_email: str, subject: str, body_html: str,
             body_text: str = None) -> None:
        config = EmailConfigService().get()
        if not config.is_enabled:
            raise EmailNotConfiguredError(
                "Email sending is disabled. Enable it and configure SMTP "
                "settings under System Administration -> Email Configuration.")
        if not config.smtp_host or not config.from_email:
            raise EmailNotConfiguredError(
                "SMTP Host and From Email must be configured before emails "
                "can be sent. See System Administration -> Email Configuration.")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = (f"{config.from_name} <{config.from_email}>"
                       if config.from_name else config.from_email)
        msg["To"] = to_email
        msg.set_content(body_text or _strip_html(body_html))
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        with smtplib.SMTP(config.smtp_host, config.smtp_port or 587) as server:
            if config.use_tls:
                server.starttls()
            if config.smtp_username:
                server.login(config.smtp_username, config.smtp_password)
            server.send_message(msg)


def _strip_html(html: str) -> str:
    """Crude fallback plain-text body when no explicit text version is
    given — good enough for a notification email, not meant to be a
    general-purpose HTML-to-text converter."""
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()
