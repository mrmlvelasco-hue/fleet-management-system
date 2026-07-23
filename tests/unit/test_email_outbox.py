"""Tests for the EmailOutbox drain -- the out-of-band delivery that
replaced sending SMTP inside the web request.
"""
from unittest.mock import patch

import pytest

from app.modules.system_admin.models import EmailOutbox
from app.modules.system_admin.services.email_outbox_service import (
    EmailOutboxService, MAX_ATTEMPTS)


def _queue(db, email="a@example.com", status="PENDING"):
    row = EmailOutbox(to_email=email, subject="Test", event_code="submitted",
                      reference_table="maintenance_orders", reference_id=1,
                      status=status)
    db.session.add(row)
    db.session.commit()
    return row


def test_send_pending_marks_row_sent_on_success(db):
    row = _queue(db)
    with patch("app.modules.system_admin.tasks._send_notification_email_impl"):
        stats = EmailOutboxService().send_pending()
    assert stats["sent"] == 1
    assert row.status == "SENT"
    assert row.sent_at is not None


def test_failed_send_stays_pending_for_retry(db):
    """A transient SMTP outage must DELAY mail, not lose it -- the row
    stays PENDING so the next scheduled run tries again."""
    row = _queue(db)
    with patch("app.modules.system_admin.tasks._send_notification_email_impl",
               side_effect=OSError("smtp down")):
        stats = EmailOutboxService().send_pending()
    assert stats["failed"] == 1
    assert row.status == "PENDING"
    assert row.attempts == 1
    assert "smtp down" in row.last_error


def test_gives_up_after_max_attempts(db):
    """One permanently-bad address must not be retried forever on every
    run, drowning out deliverable mail behind it."""
    row = _queue(db)
    row.attempts = MAX_ATTEMPTS - 1
    db.session.commit()
    with patch("app.modules.system_admin.tasks._send_notification_email_impl",
               side_effect=OSError("bad address")):
        EmailOutboxService().send_pending()
    assert row.status == "FAILED"
    assert row.attempts == MAX_ATTEMPTS


def test_exhausted_rows_are_not_picked_up_again(db):
    row = _queue(db)
    row.attempts = MAX_ATTEMPTS
    db.session.commit()
    assert EmailOutboxService().pending() == []


def test_empty_queue_returns_immediately(db):
    """Safe to run every minute -- nothing pending means no work and no
    SMTP connection."""
    with patch("app.modules.system_admin.tasks._send_notification_email_impl") as impl:
        stats = EmailOutboxService().send_pending()
    assert stats["attempted"] == 0
    assert not impl.called


def test_one_failure_does_not_abort_the_rest_of_the_batch(db):
    _queue(db, "first@example.com")
    _queue(db, "second@example.com")
    calls = {"n": 0}

    def _impl(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("first one fails")

    with patch("app.modules.system_admin.tasks._send_notification_email_impl",
               side_effect=_impl):
        stats = EmailOutboxService().send_pending()
    assert stats["attempted"] == 2
    assert stats["sent"] == 1
    assert stats["failed"] == 1


def test_retry_failed_requeues_and_resets_attempts(db):
    row = _queue(db, status="FAILED")
    row.attempts = MAX_ATTEMPTS
    db.session.commit()

    count = EmailOutboxService().retry_failed()
    assert count == 1
    assert row.status == "PENDING"
    assert row.attempts == 0


def test_summary_counts_by_status(db):
    _queue(db, "p@example.com", status="PENDING")
    _queue(db, "s@example.com", status="SENT")
    _queue(db, "f@example.com", status="FAILED")
    s = EmailOutboxService().summary()
    assert s == {"pending": 1, "sent": 1, "failed": 1}
