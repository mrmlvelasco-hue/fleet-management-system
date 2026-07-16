"""Notification Engine — subscribes to ApprovalEngine events (1b hooks),
resolves recipients from NotificationRule rows, writes InAppNotification rows,
and queues email tasks via Celery (graceful fallback if Redis unavailable)."""
from datetime import datetime, timezone

from app.extensions import db
from app.modules.system_admin.models import (
    NotificationRule, InAppNotification)
from app.modules.user_management.models import User


class NotificationEngine:
    def dispatch(self, event_name: str,
                 instance) -> None:
        """Fan out to all NotificationRule rows matching event_code."""
        rules = NotificationRule.query.filter_by(
            event_code=event_name, is_active=True).all()
        for rule in rules:
            recipients = self._resolve_recipients(rule, instance)
            for user in recipients:
                if rule.channel in ("IN_APP", "BOTH"):
                    self._send_in_app(user, event_name, instance)
                if rule.channel in ("EMAIL", "BOTH"):
                    self._queue_email(user, event_name, instance)

    def _resolve_recipients(self, rule: NotificationRule,
                             instance) -> list:
        if rule.recipient_type == "SUBMITTER":
            user = db.session.get(User, instance.submitted_by)
            return [user] if user else []

        if rule.recipient_type == "CURRENT_APPROVER":
            users = []
            if instance.approval_path:
                for level in instance.approval_path.levels:
                    if level.level_number == instance.current_level:
                        if level.approver_type == "ROLE" and level.role_id:
                            users = User.query.filter(
                                User.roles.any(id=level.role_id),
                                User.is_active.is_(True)).all()
                        elif level.approver_type == "USER" and level.user_id:
                            u = db.session.get(User, level.user_id)
                            if u:
                                users = [u]
            return users

        if rule.recipient_type == "ROLE" and rule.role_id:
            return User.query.filter(
                User.roles.any(id=rule.role_id),
                User.is_active.is_(True)).all()

        if rule.recipient_type == "SPECIFIC_USER" and rule.user_id:
            u = db.session.get(User, rule.user_id)
            return [u] if u else []

        return []

    def _send_in_app(self, user: User, event_name: str, instance) -> None:
        title = f"Document {event_name.replace('_', ' ').title()}"
        message = (f"{instance.document_type.code} "
                   f"#{instance.reference_id} - {event_name}")
        notif = InAppNotification(
            user_id=user.id, title=title, message=message,
            event_code=event_name,
            reference_table=instance.reference_table,
            reference_id=instance.reference_id)
        db.session.add(notif)
        db.session.commit()

    def _queue_email(self, user: User, event_name: str, instance) -> None:
        """Queue async email via Celery. Logs warning if broker unavailable."""
        try:
            from app.modules.system_admin.tasks import send_notification_email
            send_notification_email.delay(
                user_id=user.id, event_code=event_name,
                reference_table=instance.reference_table,
                reference_id=instance.reference_id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Email task could not be queued (broker unavailable?). "
                "In-app notification was still delivered.")


class InAppNotificationService:
    def list_for_user(self, user: User, limit: int = 20) -> list:
        return (InAppNotification.query
                .filter_by(user_id=user.id, is_active=True)
                .order_by(InAppNotification.id.desc())
                .limit(limit).all())

    def unread_count(self, user: User) -> int:
        return InAppNotification.query.filter_by(
            user_id=user.id, is_read=False, is_active=True).count()

    def mark_read(self, notification_id: int, user: User) -> None:
        notif = db.session.get(InAppNotification, notification_id)
        if notif and notif.user_id == user.id:
            notif.is_read = True
            notif.read_at = datetime.now(timezone.utc)
            db.session.commit()

    def mark_all_read(self, user: User) -> None:
        (InAppNotification.query
         .filter_by(user_id=user.id, is_read=False)
         .update({"is_read": True,
                  "read_at": datetime.now(timezone.utc)}))
        db.session.commit()


def register_notification_hooks() -> None:
    """Subscribe the NotificationEngine to ApprovalEngine events.
    Called once in the app factory."""
    from app.core.approval.engine import _subscribers
    engine = NotificationEngine()
    _subscribers.append(
        lambda event_name, instance: engine.dispatch(event_name, instance))
