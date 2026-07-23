"""Email outbox drain.

Delivers queued EmailOutbox rows out-of-band, so no web request ever
waits on SMTP. Run it from cron / Task Scheduler:

    flask email send-pending

or on a schedule via Celery beat. Safe to run every minute: if there is
nothing PENDING it returns immediately without opening a connection.
"""
import logging
from datetime import datetime, timezone

from app.extensions import db
from app.modules.system_admin.models import EmailOutbox

logger = logging.getLogger(__name__)

# After this many failed attempts a message stops being retried, so one
# permanently-bad address (typo, closed mailbox) can't be retried forever
# on every run and drown out the genuinely deliverable mail behind it.
MAX_ATTEMPTS = 5


class EmailOutboxService:

    def pending(self, limit: int = 50) -> list:
        """Oldest first, so the queue drains in the order events
        happened rather than newest-first."""
        return (EmailOutbox.query
               .filter(EmailOutbox.status == "PENDING",
                      EmailOutbox.attempts < MAX_ATTEMPTS)
               .order_by(EmailOutbox.id.asc())
               .limit(limit).all())

    def send_pending(self, limit: int = 50) -> dict:
        """Attempt delivery of up to `limit` queued messages.

        Each message is committed individually: a failure on one must not
        roll back the successful sends that came before it in the same
        run, and must not stop the rest of the batch from being tried.
        """
        from app.modules.system_admin.tasks import (
            _send_notification_email_impl)

        rows = self.pending(limit=limit)
        stats = {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0}
        if not rows:
            return stats

        for row in rows:
            stats["attempted"] += 1
            row.attempts = (row.attempts or 0) + 1
            try:
                if row.event_code == "CUSTOM_REPORT":
                    self._send_custom_report(row)
                else:
                    # Re-render from live data at send time -- the
                    # underlying document may have moved on since
                    # queueing, and the existing impl already knows how
                    # to build the full templated body for each event
                    # type.
                    _send_notification_email_impl(
                        row.to_user_id, row.event_code, row.reference_table,
                        row.reference_id, row.comment_id)
                row.status = "SENT"
                row.sent_at = datetime.now(timezone.utc)
                row.last_error = None
                stats["sent"] += 1
            except Exception as exc:
                row.last_error = str(exc)[:2000]
                # Keep it PENDING so the next run retries, until the
                # attempt ceiling is hit -- a transient SMTP outage should
                # delay mail, not lose it.
                if row.attempts >= MAX_ATTEMPTS:
                    row.status = "FAILED"
                    logger.error(
                        "Giving up on outbox email id=%s to=%s after %s "
                        "attempts: %s", row.id, row.to_email, row.attempts,
                        exc)
                else:
                    logger.warning(
                        "Outbox email id=%s to=%s failed (attempt %s), will "
                        "retry: %s", row.id, row.to_email, row.attempts, exc)
                stats["failed"] += 1
            db.session.commit()

        return stats

    def _send_custom_report(self, row) -> None:
        """Generate a saved custom report and email it as an Excel
        attachment.

        Regenerated HERE, at send time, rather than being stored on the
        queue row: keeps potentially large spreadsheets out of the
        database, and means a scheduled delivery carries current data
        instead of a snapshot from when the schedule was created.

        Runs with user=None so the report is generated with full data
        access -- the permission check already happened when a person
        with the right permission requested or scheduled the delivery,
        and the drain itself runs unattended with no session.
        """
        from app.extensions import db as _db
        from app.modules.system_admin.models import CustomReport
        from app.modules.system_admin.services.custom_report_service import (
            CustomReportService)
        from app.modules.system_admin.services.email_config_service import (
            EmailSenderService)

        report = _db.session.get(CustomReport, row.reference_id)
        if report is None:
            raise ValueError(
                f"Custom report {row.reference_id} no longer exists.")
        filename, data = CustomReportService().to_excel(report, user=None)
        EmailSenderService().send(
            to_email=row.to_email,
            subject=row.subject,
            body_html=row.body_html or f"<p>{report.name}</p>",
            attach_files=[{
                "data": data, "filename": filename,
                "mime_type": "application/vnd.openxmlformats-officedocument"
                             ".spreadsheetml.sheet",
            }])

    def summary(self) -> dict:
        """Counts per status, for the admin screen and for a quick
        health check from the command line."""
        return {
            "pending": EmailOutbox.query.filter_by(status="PENDING").count(),
            "sent": EmailOutbox.query.filter_by(status="SENT").count(),
            "failed": EmailOutbox.query.filter_by(status="FAILED").count(),
        }

    def retry_failed(self) -> int:
        """Put FAILED rows back in the queue (e.g. after fixing the SMTP
        settings), resetting their attempt count."""
        rows = EmailOutbox.query.filter_by(status="FAILED").all()
        for row in rows:
            row.status = "PENDING"
            row.attempts = 0
            row.last_error = None
        db.session.commit()
        return len(rows)
