"""Celery tasks for the Notification Engine.

Email sending (SMTP config) ships in Phase 5. This task is wired now so
the Notification Engine can queue it; it logs the intent but does not
actually send until SMTP is configured.
"""
import logging

from app.core.celery_app import celery

logger = logging.getLogger(__name__)


@celery.task(name="system_admin.send_notification_email", bind=True,
             max_retries=3, default_retry_delay=60)
def send_notification_email(self, user_id: int, event_code: str,
                             reference_table: str, reference_id: int):
    """Queue an email notification. SMTP delivery wired in Phase 5."""
    logger.info(
        "Email notification queued (SMTP not yet configured): "
        "user=%s event=%s ref=%s/%s",
        user_id, event_code, reference_table, reference_id)
