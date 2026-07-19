"""Celery tasks for the Notification Engine.

Email delivery now actually sends via SMTP (EmailSenderService), gated
behind the admin-configurable EmailConfig.is_enabled switch -- this
replaces the earlier stub that only logged intent.

IMPORTANT: the actual email-sending logic lives in the plain function
`_send_notification_email_impl` below, NOT inside the Celery task body.
The Celery task is a thin wrapper around it. This lets callers (the
Notification Engine, the comment service) fall back to calling the impl
function directly -- synchronously, in the same request -- when
`.delay()` can't reach the broker, which is the normal situation on a
dev machine where no separate `celery worker` process is running.
Without this, every real notification (approvals, comment mentions)
silently dropped: `.delay()` would fail, get caught, and just log a
warning, with nothing ever actually sent -- while the unrelated "Send
Test Email" button worked fine because IT already had this same
fallback. Now both paths behave identically.
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


@celery.task(name="system_admin.send_test_email", bind=True,
             max_retries=0)
def send_test_email(self, to_email: str):
    """Send a one-off SMTP test message. Kept separate from
    send_notification_email so a failing test never retries three times
    against a broken server and so the admin gets the real error string."""
    from app.modules.system_admin.services.email_config_service import (
        EmailSenderService)
    EmailSenderService().send(
        to_email=to_email,
        subject="Fleet Management System — Test Email",
        body_html=(
            "<p>This is a test email confirming your SMTP configuration "
            "is working correctly.</p>"
            f"<p>Delivery target: {to_email}</p>"),
        body_text=("This is a test email confirming your SMTP configuration "
                   "is working correctly."))
    logger.info("Test email sent to %s", to_email)


def _build_notification_context(user, event_code, reference_table,
                                reference_id, comment_id=None) -> dict:
    """Everything a template might want to reference. Kept in one place
    so every template (built-in or admin-added) has access to the same
    fields regardless of event type."""
    from app.core.reference_resolver import get_document_number, get_view_url

    context = {
        "recipient_name": user.full_name if hasattr(user, "full_name") else user.username,
        "reference_table": reference_table,
        "reference_id": reference_id,
        "event_code": event_code,
        "event_label": event_code.replace("_", " ").title(),
        # Real document number (e.g. "MO-2026-000011") instead of the raw
        # numeric reference_id -- this was previously the only thing
        # every email showed, which is meaningless to a recipient.
        "document_number": get_document_number(reference_table, reference_id),
        "view_url": get_view_url(reference_table, reference_id) or "",
        "comment_body": "",
        "author_name": "",
    }

    if comment_id is not None:
        from app.core.comments.models import DocumentComment
        comment = db.session.get(DocumentComment, comment_id)
        if comment is not None:
            context["comment_body"] = comment.body
            author = getattr(comment, "author", None)
            context["author_name"] = (
                author.full_name if author and hasattr(author, "full_name")
                else (author.username if author else "Someone"))
    return context


def _resolve_comment_attachments(comment_id: int) -> list:
    """Files attached to a comment, in the shape EmailSenderService
    expects, resolved to an absolute path on disk."""
    if comment_id is None:
        return []
    import os
    from flask import current_app
    from app.core.attachments.attachment_service import AttachmentService
    attachments = AttachmentService().list_for("document_comments", comment_id)
    out = []
    for att in attachments:
        filepath = os.path.join(current_app.instance_path, "uploads",
                                "document_comments", att.filename)
        out.append({"filepath": filepath, "filename": att.original_filename,
                    "mime_type": att.mime_type})
    return out


def _send_notification_email_impl(user_id: int, event_code: str,
                                   reference_table: str, reference_id: int,
                                   comment_id: int = None) -> None:
    """The actual work: render the template and send via SMTP. Plain
    function (no Celery `self`/retry) so it can be called directly as a
    synchronous fallback, not only through the Celery task below."""
    from app.modules.user_management.models import User
    from app.modules.system_admin.models import EmailTemplate

    user = db.session.get(User, user_id)
    if user is None or not user.email:
        logger.info("Skipping email for user=%s (not found or no email "
                   "address) — in-app notification still delivered.", user_id)
        return

    context = _build_notification_context(
        user, event_code, reference_table, reference_id, comment_id)

    template = EmailTemplate.query.filter_by(event_code=event_code).first()
    subject_src = template.subject if template else _FALLBACK_SUBJECT
    body_html_src = template.body_html if template and template.body_html else _FALLBACK_BODY_HTML
    body_text_src = template.body_text if template and template.body_text else _FALLBACK_BODY_TEXT

    subject = Template(subject_src).render(**context)
    body_html = Template(body_html_src).render(**context)
    body_text = Template(body_text_src).render(**context)

    attach_files = _resolve_comment_attachments(comment_id)

    try:
        EmailSenderService().send(to_email=user.email, subject=subject,
                                  body_html=body_html, body_text=body_text,
                                  attach_files=attach_files)
        logger.info("Email notification sent: user=%s event=%s ref=%s/%s",
                   user_id, event_code, reference_table, reference_id)
    except EmailNotConfiguredError as e:
        # Not an error worth retrying or alarming about — email just isn't
        # turned on. The in-app notification already reached the person.
        logger.info("Email not sent (not configured): %s", e)


@celery.task(name="system_admin.send_notification_email", bind=True,
             max_retries=3, default_retry_delay=60)
def send_notification_email(self, user_id: int, event_code: str,
                             reference_table: str, reference_id: int,
                             comment_id: int = None):
    try:
        _send_notification_email_impl(
            user_id, event_code, reference_table, reference_id, comment_id)
    except Exception as e:
        logger.exception(
            "Failed to send email notification: user=%s event=%s ref=%s/%s: %s",
            user_id, event_code, reference_table, reference_id, e)
        raise self.retry(exc=e)


def dispatch_notification_email(user_id: int, event_code: str,
                                reference_table: str, reference_id: int,
                                comment_id: int = None) -> None:
    """Single entry point used by every caller that wants a notification
    email sent (NotificationEngine, CommentService, ...). Tries to queue
    via Celery first (fast, non-blocking); if the broker is unreachable —
    the common case with no `celery worker` process running — falls back
    to sending synchronously in the current request via the same impl
    function, so the email actually goes out either way instead of
    silently disappearing after a logged warning.

    `comment_id`, when given (comment-mention notifications only), lets
    the email include the actual comment text and any files attached to
    that specific comment."""
    try:
        send_notification_email.delay(
            user_id=user_id, event_code=event_code,
            reference_table=reference_table, reference_id=reference_id,
            comment_id=comment_id)
    except Exception:
        logger.info(
            "Celery broker unavailable for notification email (event=%s, "
            "user=%s) — sending synchronously instead.", event_code, user_id)
        try:
            _send_notification_email_impl(
                user_id, event_code, reference_table, reference_id,
                comment_id)
        except Exception:
            logger.exception(
                "Synchronous fallback also failed to send notification "
                "email: user=%s event=%s ref=%s/%s",
                user_id, event_code, reference_table, reference_id)
