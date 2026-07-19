from unittest.mock import patch, MagicMock

import pytest

from app.modules.system_admin.services.email_config_service import (
    EmailConfigService, EmailSenderService, EmailNotConfiguredError)


def test_get_creates_singleton_row_if_none_exists(db):
    config = EmailConfigService().get()
    assert config is not None
    assert config.is_enabled is False  # safe default: disabled until configured


def test_get_returns_same_row_on_repeated_calls(db):
    first = EmailConfigService().get()
    second = EmailConfigService().get()
    assert first.id == second.id


def test_update_sets_smtp_fields(db):
    EmailConfigService().update(
        smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="fleet@example.com", smtp_password="secret123",
        use_tls=True, from_email="fleet@example.com",
        from_name="Example Fleet", is_enabled=True)
    config = EmailConfigService().get()
    assert config.smtp_host == "smtp.example.com"
    assert config.smtp_port == 587
    assert config.is_enabled is True


def test_send_raises_when_disabled(db):
    EmailConfigService().update(smtp_host="smtp.example.com", is_enabled=False)
    with pytest.raises(EmailNotConfiguredError):
        EmailSenderService().send(
            to_email="someone@example.com", subject="Test",
            body_html="<p>Hello</p>")


def test_send_raises_when_no_host_configured(db):
    EmailConfigService().update(is_enabled=True, smtp_host=None)
    with pytest.raises(EmailNotConfiguredError):
        EmailSenderService().send(
            to_email="someone@example.com", subject="Test",
            body_html="<p>Hello</p>")


@patch("app.modules.system_admin.services.email_config_service.smtplib.SMTP")
def test_send_uses_configured_smtp_settings(mock_smtp_cls, db):
    EmailConfigService().update(
        smtp_host="smtp.example.com", smtp_port=587,
        smtp_username="fleet@example.com", smtp_password="secret123",
        use_tls=True, from_email="fleet@example.com",
        from_name="Example Fleet", is_enabled=True)

    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    EmailSenderService().send(
        to_email="approver@example.com", subject="Document Pending Approval",
        body_html="<p>Please review MO-2026-000001</p>",
        body_text="Please review MO-2026-000001")

    # 587 = submission port → plain SMTP + STARTTLS, now with a timeout so
    # a dead server can't hang the request/worker indefinitely.
    mock_smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=10)
    mock_server.ehlo.assert_called()
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("fleet@example.com", "secret123")
    assert mock_server.send_message.call_count == 1
    sent_msg = mock_server.send_message.call_args[0][0]
    assert sent_msg["To"] == "approver@example.com"
    assert sent_msg["Subject"] == "Document Pending Approval"
    assert "Example Fleet" in sent_msg["From"]


@patch("app.modules.system_admin.services.email_config_service.smtplib.SMTP_SSL")
@patch("app.modules.system_admin.services.email_config_service.smtplib.SMTP")
def test_send_uses_ssl_on_port_465(mock_smtp, mock_smtp_ssl, db):
    """Port 465 is implicit SSL — must use SMTP_SSL and must NOT call
    starttls(). Using plain SMTP()+starttls() against 465 hangs forever
    (the original 'browser keeps loading' bug). Regression guard."""
    EmailConfigService().update(
        smtp_host="smtp.hostinger.com", smtp_port=465,
        smtp_username="admin@example.com", smtp_password="secret",
        use_tls=False, from_email="admin@example.com",
        from_name="Fleet", is_enabled=True)
    ssl_server = MagicMock()
    mock_smtp_ssl.return_value.__enter__.return_value = ssl_server

    EmailSenderService().send(to_email="a@example.com", subject="Hi",
                              body_html="<p>Hi</p>")

    # SMTP_SSL used with timeout; plain SMTP never touched; no STARTTLS.
    assert mock_smtp_ssl.call_args[0][:2] == ("smtp.hostinger.com", 465)
    assert mock_smtp_ssl.call_args[1]["timeout"] == 10
    mock_smtp.assert_not_called()
    ssl_server.starttls.assert_not_called()
    ssl_server.login.assert_called_once()


@patch("app.modules.system_admin.services.email_config_service.smtplib.SMTP")
def test_send_skips_starttls_and_login_when_not_configured(mock_smtp_cls, db):
    EmailConfigService().update(
        smtp_host="smtp.example.com", smtp_port=25,
        use_tls=False, smtp_username=None, smtp_password=None,
        from_email="fleet@example.com", is_enabled=True)
    mock_server = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_server

    EmailSenderService().send(to_email="a@example.com", subject="Hi",
                              body_html="<p>Hi</p>")

    mock_server.starttls.assert_not_called()
    mock_server.login.assert_not_called()
