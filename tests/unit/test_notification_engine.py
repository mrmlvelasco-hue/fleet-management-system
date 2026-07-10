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
