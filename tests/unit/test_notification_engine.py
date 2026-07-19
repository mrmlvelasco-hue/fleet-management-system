import pytest
from app.modules.system_admin.services.notification_engine import (
    NotificationEngine)
from app.modules.system_admin.models import (
    NotificationRule, InAppNotification)
from app.modules.user_management.models import User, Role
from app.core.approval.models import ApprovalInstance
from app.modules.document_config.models import DocumentType
from app.modules.approval_config.models import ApprovalPath


@pytest.fixture()
def env(db):
    role = Role(name="Supervisor")
    submitter = User(username="sub", email="s@x.com", password_hash="x")
    approver = User(username="appr", email="a@x.com", password_hash="x")
    approver.roles.append(role)
    db.session.add_all([role, submitter, approver])
    db.session.commit()

    dt = DocumentType(code="PR", name="Purchase Request")
    path = ApprovalPath(name="One-Step")
    db.session.add_all([dt, path])
    db.session.flush()

    instance = ApprovalInstance(
        document_type_id=dt.id, reference_table="purchase_requests",
        reference_id=1, status="PENDING", current_level=1,
        submitted_by=submitter.id, approval_path_id=path.id)
    db.session.add(instance)
    db.session.commit()
    return submitter, approver, role, instance


def test_in_app_notification_created_for_submitter(db, env):
    submitter, approver, role, instance = env
    db.session.add(NotificationRule(
        event_code="submitted", channel="IN_APP",
        recipient_type="SUBMITTER"))
    db.session.commit()
    eng = NotificationEngine()
    eng.dispatch("submitted", instance)
    notifs = InAppNotification.query.filter_by(
        user_id=submitter.id).all()
    assert len(notifs) == 1
    assert notifs[0].event_code == "submitted"


def test_in_app_notification_created_for_role_recipients(db, env):
    submitter, approver, role, instance = env
    db.session.add(NotificationRule(
        event_code="submitted", channel="IN_APP",
        recipient_type="ROLE", role_id=role.id))
    db.session.commit()
    NotificationEngine().dispatch("submitted", instance)
    assert InAppNotification.query.filter_by(
        user_id=approver.id).count() == 1


def test_no_rule_means_no_notification(db, env):
    _, _, _, instance = env
    NotificationEngine().dispatch("submitted", instance)
    assert InAppNotification.query.count() == 0


def test_unread_count(db, env):
    submitter, _, _, instance = env
    db.session.add(NotificationRule(
        event_code="submitted", channel="IN_APP",
        recipient_type="SUBMITTER"))
    db.session.commit()
    NotificationEngine().dispatch("submitted", instance)
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    assert InAppNotificationService().unread_count(submitter) == 1


def test_mark_read(db, env):
    submitter, _, _, instance = env
    db.session.add(NotificationRule(
        event_code="submitted", channel="IN_APP",
        recipient_type="SUBMITTER"))
    db.session.commit()
    NotificationEngine().dispatch("submitted", instance)
    notif = InAppNotification.query.filter_by(user_id=submitter.id).first()
    from app.modules.system_admin.services.notification_engine import (
        InAppNotificationService)
    InAppNotificationService().mark_read(notif.id, submitter)
    assert InAppNotification.query.get(notif.id).is_read is True


def test_email_actually_sends_when_celery_broker_unreachable(db, env, monkeypatch):
    """Regression test for the bug where BOTH/EMAIL notification rules
    silently sent nothing: _queue_email only tried task.delay() and
    swallowed the resulting exception with a bare warning log when Celery
    couldn't reach its broker (the normal case with no separate `celery
    worker` process running) -- the email was never actually sent. This
    locks in the fix: dispatch_notification_email must fall back to
    sending synchronously so the email goes out either way."""
    from unittest.mock import patch, MagicMock
    submitter, _, _, instance = env
    db.session.add(NotificationRule(
        event_code="submitted", channel="BOTH",
        recipient_type="SUBMITTER"))
    db.session.commit()

    from app.modules.system_admin.services.email_config_service import (
        EmailConfigService)
    EmailConfigService().update(
        smtp_host="smtp.example.com", smtp_port=587, use_tls=True,
        smtp_username="u", smtp_password="p", from_email="fms@example.com",
        is_enabled=True)

    # Simulate .delay() being unable to reach a broker at all -- exactly
    # what happens with no Celery worker/Redis running.
    from app.modules.system_admin import tasks as tasks_module
    monkeypatch.setattr(
        tasks_module.send_notification_email, "delay",
        MagicMock(side_effect=ConnectionRefusedError("no broker")))

    with patch("smtplib.SMTP") as mock_smtp:
        srv = MagicMock()
        mock_smtp.return_value.__enter__.return_value = srv
        NotificationEngine().dispatch("submitted", instance)

    assert srv.send_message.called, (
        "Email was never actually sent when the Celery broker was "
        "unreachable -- the synchronous fallback did not run.")
    sent_msg = srv.send_message.call_args[0][0]
    assert sent_msg["To"] == submitter.email
