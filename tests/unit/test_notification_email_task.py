from unittest.mock import patch

from app.core.security.password import hash_password
from app.modules.user_management.models import User
from app.modules.system_admin.models import EmailTemplate
from app.modules.system_admin.services.email_config_service import (
    EmailConfigService)
from app.modules.system_admin.tasks import send_notification_email


def _make_user(db):
    u = User(username="email_task_user", email="email_task_user@x.com",
             password_hash=hash_password("pw123456"),
             first_name="Ana", last_name="Cruz")
    db.session.add(u)
    db.session.commit()
    return u


def test_task_does_nothing_when_email_disabled(db):
    """Safe no-op, not an error, when email sending isn't configured --
    the in-app notification already covers the person regardless. Uses
    the REAL EmailSenderService (default config is_enabled=False) rather
    than mocking .send() itself, so this actually exercises the
    EmailNotConfiguredError handling path inside the task."""
    user = _make_user(db)
    # No EmailConfigService().update() call — config defaults to disabled.
    result = send_notification_email(
        user_id=user.id, event_code="pm_overdue",
        reference_table="maintenance_orders", reference_id=1)
    assert result is None  # completes without raising


@patch("app.modules.system_admin.tasks.EmailSenderService.send")
def test_task_sends_using_configured_template(mock_send, db):
    user = _make_user(db)
    EmailConfigService().update(
        smtp_host="smtp.example.com", is_enabled=True,
        from_email="fleet@example.com")
    EmailTemplate(event_code="pm_overdue", name="PM Overdue",
                 subject="PM Overdue: {{ reference_table }} #{{ reference_id }}",
                 body_html="<p>Hi {{ recipient_name }}, item is overdue.</p>",
                 body_text="Hi {{ recipient_name }}, item is overdue.")
    from app.extensions import db as _db
    tmpl = EmailTemplate.query.filter_by(event_code="pm_overdue").first()
    if tmpl is None:
        tmpl = EmailTemplate(event_code="pm_overdue", name="PM Overdue",
                             subject="PM Overdue: {{ reference_table }} #{{ reference_id }}",
                             body_html="<p>Hi {{ recipient_name }}, item is overdue.</p>",
                             body_text="Hi {{ recipient_name }}, item is overdue.")
        _db.session.add(tmpl)
        _db.session.commit()

    send_notification_email(
        user_id=user.id, event_code="pm_overdue",
        reference_table="maintenance_orders", reference_id=42)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_email"] == "email_task_user@x.com"
    assert "maintenance_orders" in kwargs["subject"]
    assert "42" in kwargs["subject"]
    assert "Ana" in kwargs["body_html"]


@patch("app.modules.system_admin.tasks.EmailSenderService.send")
def test_task_falls_back_to_generic_content_when_no_template_configured(mock_send, db):
    user = _make_user(db)
    EmailConfigService().update(
        smtp_host="smtp.example.com", is_enabled=True,
        from_email="fleet@example.com")

    send_notification_email(
        user_id=user.id, event_code="pm_due_soon",
        reference_table="maintenance_orders", reference_id=7)

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to_email"] == "email_task_user@x.com"
    assert kwargs["subject"]  # non-empty fallback subject
    assert kwargs["body_html"]


def test_task_handles_unknown_user_gracefully(db):
    """Shouldn't raise if the user was deleted/deactivated between
    dispatch and the task actually running."""
    with patch("app.modules.system_admin.tasks.EmailSenderService.send") as mock_send:
        send_notification_email(
            user_id=999999, event_code="pm_overdue",
            reference_table="maintenance_orders", reference_id=1)
        mock_send.assert_not_called()
