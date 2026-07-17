"""Celery tasks for the Notification Engine.

Email delivery now actually sends via SMTP (EmailSenderService), gated
behind the admin-configurable EmailConfig.is_enabled switch -- this
replaces the earlier stub that only logged intent.
"""
import logging

from jinja2 import Template

from app.core.celery_app import celery
from app.extensions import db
from app.modules.system_admin.services.email_config_service import (
    EmailSenderService, EmailNotConfiguredError)

logger = logging.getLogger(__name__)

_FALLBACK_SUBJECT = "{{ event_label }}: {{ reference_table }} #{{ reference_id }}"
_FALLBACK_BODY_HTML = (
    "<p>Hello {{ recipient_name }},</p>"
    "<p>This is a notification regarding <strong>{{ reference_table }} "
    "#{{ reference_id }}</strong> ({{ event_label }}).</p>"
    "<p>Please log in to the Fleet Management System to view details.</p>")
_FALLBACK_BODY_TEXT = (
    "Hello {{ recipient_name }},\n\n"
    "This is a notification regarding {{ reference_table }} "
    "#{{ reference_id }} ({{ event_label }}).\n\n"
    "Please log in to the Fleet Management System to view details.")


@celery.task(name="system_admin.send_notification_email", bind=True,
             max_retries=3, default_retry_delay=60)
def send_notification_email(self, user_id: int, event_code: str,
                             reference_table: str, reference_id: int):
    from app.modules.user_management.models import User
    from app.modules.system_admin.models import EmailTemplate

    user = db.session.get(User, user_id)
    if user is None or not user.email:
        logger.info("Skipping email for user=%s (not found or no email "
                   "address) — in-app notification still delivered.", user_id)
        return

    context = {
        "recipient_name": user.full_name if hasattr(user, "full_name") else user.username,
        "reference_table": reference_table,
        "reference_id": reference_id,
        "event_code": event_code,
        "event_label": event_code.replace("_", " ").title(),
    }

    template = EmailTemplate.query.filter_by(event_code=event_code).first()
    subject_src = template.subject if template else _FALLBACK_SUBJECT
    body_html_src = template.body_html if template and template.body_html else _FALLBACK_BODY_HTML
    body_text_src = template.body_text if template and template.body_text else _FALLBACK_BODY_TEXT

    subject = Template(subject_src).render(**context)
    body_html = Template(body_html_src).render(**context)
    body_text = Template(body_text_src).render(**context)

    try:
        EmailSenderService().send(to_email=user.email, subject=subject,
                                  body_html=body_html, body_text=body_text)
        logger.info("Email notification sent: user=%s event=%s ref=%s/%s",
                   user_id, event_code, reference_table, reference_id)
    except EmailNotConfiguredError as e:
        # Not an error worth retrying or alarming about — email just isn't
        # turned on. The in-app notification already reached the person.
        logger.info("Email not sent (not configured): %s", e)
    except Exception as e:
        logger.exception(
            "Failed to send email notification: user=%s event=%s ref=%s/%s: %s",
            user_id, event_code, reference_table, reference_id, e)
        raise self.retry(exc=e)
