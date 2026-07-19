"""Tests for ScheduledReportService — the `flask report run-due` engine.

Covers: due-schedule detection, successful send with a real attachment,
next_run_at advancement per frequency, graceful failure (bad config)
without blocking other schedules, and the run_due(only_id=...) precision
used by the "Run Now" button.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from app.extensions import db
from app.modules.system_admin.models import ScheduledReport
from app.modules.system_admin.services.email_config_service import (
    EmailConfigService)
from app.modules.system_admin.services.scheduled_report_service import (
    ScheduledReportService)


@pytest.fixture()
def email_configured(db):
    EmailConfigService().update(
        smtp_host="smtp.example.com", smtp_port=587, use_tls=True,
        smtp_username="u", smtp_password="p", from_email="fms@example.com",
        is_enabled=True)


def _due_schedule(db, **overrides):
    defaults = dict(
        name="Test Schedule", report_code="RPT_MAINTENANCE_COST_SUMMARY",
        frequency="WEEKLY", recipients="a@example.com,b@example.com",
        next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    defaults.update(overrides)
    sched = ScheduledReport(**defaults)
    db.session.add(sched)
    db.session.commit()
    return sched


def test_run_due_sends_to_every_recipient_with_attachment(
        db, email_configured):
    sched = _due_schedule(db)
    with patch("smtplib.SMTP") as mock_smtp:
        srv = MagicMock()
        mock_smtp.return_value.__enter__.return_value = srv
        sent, failed = ScheduledReportService().run_due()

    assert sent == 1
    assert failed == 0
    assert srv.send_message.call_count == 2  # one per recipient
    sent_to = {call[0][0]["To"] for call in srv.send_message.call_args_list}
    assert sent_to == {"a@example.com", "b@example.com"}
    for call in srv.send_message.call_args_list:
        msg = call[0][0]
        attachments = [p.get_filename() for p in msg.iter_attachments()]
        assert attachments and attachments[0].endswith(".xlsx")

    db.session.refresh(sched)
    assert sched.last_run_status == "SUCCESS"
    assert sched.last_run_at is not None
    # WEEKLY -> next_run_at should have advanced ~7 days past the run time.
    # Compared naively: SQLite (like MySQL's DATETIME) drops tzinfo on
    # retrieval, so both sides need to be naive here even though the
    # service itself works in UTC-aware datetimes internally.
    assert sched.next_run_at > datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=6)


def test_run_due_skips_schedules_not_yet_due(db, email_configured):
    _due_schedule(db, next_run_at=datetime.now(timezone.utc)
                 + timedelta(days=1))
    with patch("smtplib.SMTP") as mock_smtp:
        sent, failed = ScheduledReportService().run_due()
    assert (sent, failed) == (0, 0)
    mock_smtp.assert_not_called()


def test_run_due_marks_failed_without_blocking_other_schedules(
        db, email_configured):
    broken = _due_schedule(db, name="Broken", recipients="")
    healthy = _due_schedule(db, name="Healthy")

    with patch("smtplib.SMTP") as mock_smtp:
        srv = MagicMock()
        mock_smtp.return_value.__enter__.return_value = srv
        sent, failed = ScheduledReportService().run_due()

    assert sent == 1
    assert failed == 1
    db.session.refresh(broken)
    db.session.refresh(healthy)
    assert broken.last_run_status == "FAILED"
    assert healthy.last_run_status == "SUCCESS"
    # Even the failed schedule must advance past due, or it would fire
    # again on every single future run_due() call forever.
    assert broken.next_run_at > datetime.now(timezone.utc).replace(tzinfo=None)


def test_run_due_only_id_ignores_other_due_schedules(db, email_configured):
    target = _due_schedule(db, name="Target")
    _due_schedule(db, name="Also Due")

    with patch("smtplib.SMTP") as mock_smtp:
        srv = MagicMock()
        mock_smtp.return_value.__enter__.return_value = srv
        sent, failed = ScheduledReportService().run_due(only_id=target.id)

    assert sent == 1
    assert failed == 0
    # Only the target's two recipients, not the other schedule's.
    assert srv.send_message.call_count == 2


def test_create_rejects_unregistered_report_code(db):
    with pytest.raises(ValueError):
        ScheduledReportService().create(
            name="Bad", report_code="NOT_A_REAL_REPORT",
            frequency="WEEKLY", recipients="a@example.com")
