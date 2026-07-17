from unittest.mock import patch

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission


def _login(client, db, *, codes=()):
    role = Role(name="EmailConfigUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="emailconfig_user", email="emailconfig_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "emailconfig_user", "password": "pw123456"})
    return u


def test_email_config_page_renders(client, db):
    _login(client, db, codes=["emailtemplate.view"])
    resp = client.get("/admin/email-config")
    assert resp.status_code == 200
    assert b"Email Configuration" in resp.data
    assert b"Enable Email Sending" in resp.data


def test_email_config_page_shows_send_test_only_with_update_permission(client, db):
    _login(client, db, codes=["emailtemplate.view"])  # view only, no update
    resp = client.get("/admin/email-config")
    assert resp.status_code == 200
    assert b"Send Test Email" not in resp.data


def test_saving_email_config(client, db):
    _login(client, db, codes=["emailtemplate.view", "emailtemplate.update"])
    resp = client.post("/admin/email-config", data={
        "smtp_host": "smtp.example.com", "smtp_port": "587",
        "smtp_username": "fleet@example.com", "smtp_password": "secret123",
        "use_tls": "on", "from_email": "fleet@example.com",
        "from_name": "Example Fleet", "is_enabled": "on",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Email configuration updated" in resp.data

    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    config = EmailConfigService().get()
    assert config.smtp_host == "smtp.example.com"
    assert config.is_enabled is True


def test_saving_config_without_password_keeps_existing_one(client, db):
    """Blank password field means 'keep the current one' -- shouldn't
    force re-entering a working password to change an unrelated field."""
    _login(client, db, codes=["emailtemplate.view", "emailtemplate.update"])
    client.post("/admin/email-config", data={
        "smtp_host": "smtp.example.com", "smtp_port": "587",
        "smtp_username": "fleet@example.com", "smtp_password": "original_secret",
        "from_email": "fleet@example.com", "is_enabled": "on",
    })
    # Second save, no password provided, just changing the port.
    client.post("/admin/email-config", data={
        "smtp_host": "smtp.example.com", "smtp_port": "465",
        "smtp_username": "fleet@example.com", "smtp_password": "",
        "from_email": "fleet@example.com", "is_enabled": "on",
    })
    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    config = EmailConfigService().get()
    assert config.smtp_password == "original_secret"
    assert config.smtp_port == 465


@patch("app.modules.system_admin.services.email_config_service.EmailSenderService.send")
def test_send_test_email_success(mock_send, client, db):
    user = _login(client, db, codes=["emailtemplate.view", "emailtemplate.update"])
    resp = client.post("/admin/email-config/send-test", data={
        "test_email": "recipient@example.com",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Test email sent successfully" in resp.data
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to_email"] == "recipient@example.com"


@patch("app.modules.system_admin.services.email_config_service.EmailSenderService.send")
def test_send_test_email_shows_configuration_error(mock_send, client, db):
    from app.modules.system_admin.services.email_config_service import (
        EmailNotConfiguredError)
    mock_send.side_effect = EmailNotConfiguredError("Not configured yet.")
    _login(client, db, codes=["emailtemplate.view", "emailtemplate.update"])
    resp = client.post("/admin/email-config/send-test", data={
        "test_email": "recipient@example.com",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert b"Not configured yet." in resp.data
