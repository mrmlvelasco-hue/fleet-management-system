"""Scheduled report delivery — the logic behind `flask report run-due`.

Sends the Excel file produced by the report's registered generator
(app.core.reporting.generators.REPORT_GENERATORS) as an email attachment
to every recipient, then advances next_run_at to the next occurrence.
Uses the same EmailSenderService as every other email in the app, with
attach_files -- the same mechanism that carries a comment's uploaded
file along with its notification.
"""
import logging
from datetime import datetime, timezone

from app.extensions import db
from app.modules.system_admin.models import ScheduledReport

logger = logging.getLogger(__name__)


class ScheduledReportService:

    def list_all(self) -> list:
        return (ScheduledReport.query
               .filter_by(is_active=True)
               .order_by(ScheduledReport.name)
               .all())

    def create(self, *, name, report_code, frequency, recipients,
              filters=None) -> ScheduledReport:
        import json
        from app.core.reporting.generators import REPORT_GENERATORS
        if report_code not in REPORT_GENERATORS:
            raise ValueError(
                f"'{report_code}' has no registered Excel generator — it "
                f"can't be scheduled. Available: "
                f"{', '.join(REPORT_GENERATORS.keys())}")
        item = ScheduledReport(
            name=name, report_code=report_code, frequency=frequency,
            recipients=recipients,
            filters_json=json.dumps(filters) if filters else None)
        item.next_run_at = item.compute_next_run()
        db.session.add(item)
        db.session.commit()
        return item

    def delete(self, scheduled_report_id: int) -> None:
        item = db.session.get(ScheduledReport, scheduled_report_id)
        if item:
            item.is_active = False
            db.session.commit()

    def run_due(self, now=None, only_id: int = None) -> tuple:
        """Process every active schedule whose next_run_at has passed.
        Returns (sent_count, failed_count). Never lets one schedule's
        failure (bad recipient address, generator error) stop the others
        from running.

        `only_id`, when given, restricts this run to a single schedule
        (still gated on next_run_at having passed) — used by the "Run
        Now" button so forcing one schedule to fire early doesn't also
        sweep up any other schedules that happen to be due at that same
        moment, which would otherwise make the button's result
        confusing ("sent to X" when Y also just got emailed)."""
        from app.core.reporting.generators import REPORT_GENERATORS
        from app.modules.system_admin.services.email_config_service import (
            EmailNotConfiguredError)

        now = now or datetime.now(timezone.utc)
        query = (ScheduledReport.query
                .filter_by(is_active=True)
                .filter(ScheduledReport.next_run_at <= now))
        if only_id is not None:
            query = query.filter(ScheduledReport.id == only_id)
        due = query.all()

        sent, failed = 0, 0
        for sched in due:
            try:
                generator = REPORT_GENERATORS.get(sched.report_code)
                if generator is None:
                    raise ValueError(
                        f"No generator registered for '{sched.report_code}' "
                        f"(was it removed from REPORT_GENERATORS?)")
                filename, xlsx_bytes = generator(sched.filters_dict())

                recipients = sched.recipient_list()
                if not recipients:
                    raise ValueError("No recipients configured.")

                for to_email in recipients:
                    self._send_report_email(to_email, sched.name, filename,
                                            xlsx_bytes)

                sched.last_run_status = "SUCCESS"
                sent += 1
            except EmailNotConfiguredError as e:
                sched.last_run_status = "FAILED"
                logger.info("Scheduled report '%s' not sent (email not "
                          "configured): %s", sched.name, e)
                failed += 1
            except Exception as e:
                sched.last_run_status = "FAILED"
                logger.exception("Scheduled report '%s' failed: %s",
                                sched.name, e)
                failed += 1
            finally:
                sched.last_run_at = now
                sched.next_run_at = sched.compute_next_run(now)
                db.session.commit()

        return sent, failed

    def _send_report_email(self, to_email, report_name, filename,
                           xlsx_bytes) -> None:
        """Direct in-memory attach, bypassing the filepath-based
        attach_files used elsewhere (comment attachments live on disk;
        a freshly generated report doesn't need to touch disk at all)."""
        import tempfile
        import os
        from app.modules.system_admin.services.email_config_service import (
            EmailSenderService)

        # EmailSenderService.attach_files reads from a filepath (matches
        # how comment attachments already work on disk) -- writing the
        # freshly generated bytes to a short-lived temp file is simpler
        # and safer than adding a second, parallel in-memory attachment
        # code path that would need its own testing and maintenance.
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(xlsx_bytes)
            tmp_path = tmp.name
        try:
            EmailSenderService().send(
                to_email=to_email,
                subject=f"[FMS] Scheduled Report: {report_name}",
                body_html=(f"<p>Attached is your scheduled report: "
                          f"<strong>{report_name}</strong>.</p>"
                          f"<p>Generated {datetime.now():%B %d, %Y %I:%M %p}."
                          f"</p>"),
                body_text=f"Attached is your scheduled report: {report_name}.",
                attach_files=[{
                    "filepath": tmp_path, "filename": filename,
                    "mime_type": "application/vnd.openxmlformats-"
                                "officedocument.spreadsheetml.sheet"}])
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
