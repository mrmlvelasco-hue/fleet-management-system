"""Tests for `flask attachments check` and `flask attachments
backfill-from-disk` -- recovery for attachments uploaded before the
file_data column existed (migration 9230c5844ae6), whose bytes only ever
lived on that one server's disk.

Reported scenario: a database-only backup/restore to a new server left
these rows with file_data still NULL and no matching file on the new
machine's disk -- a 404 on a vehicle photo. These commands let someone
recover such a file from wherever its disk copy still exists, before
that machine is decommissioned.
"""
import os

import pytest

from app.cli import attachments_backfill_from_disk, attachments_check
from app.core.models.attachment import Attachment


@pytest.fixture()
def legacy_attachment(app, db):
    """A row with file_data NULL and a real file sitting on disk --
    exactly what an attachment uploaded before the migration looks like."""
    upload_dir = os.path.join(app.instance_path, "uploads", "vehicles")
    os.makedirs(upload_dir, exist_ok=True)
    content = b"\xff\xd8\xff\xe0FAKE_JPEG_BYTES"
    path = os.path.join(upload_dir, "legacy_test.jpg")
    with open(path, "wb") as f:
        f.write(content)

    att = Attachment(reference_table="vehicles", reference_id=1,
                     filename="legacy_test.jpg",
                     original_filename="Front_AVA-4017.jpg",
                     file_size=len(content), mime_type="image/jpeg",
                     file_data=None)
    db.session.add(att)
    db.session.commit()
    yield att, content
    if os.path.exists(path):
        os.remove(path)


def test_check_reports_disk_only_attachment(app, db, legacy_attachment,
                                            capsys):
    att, content = legacy_attachment
    with app.app_context():
        runner = app.test_cli_runner()
        result = runner.invoke(attachments_check)
    assert "Disk-only, file found on THIS machine:   1" in result.output
    assert "Database-backed (safe in any DB backup): 0" in result.output


def test_backfill_copies_bytes_from_disk_into_the_database(
        app, db, legacy_attachment):
    att, content = legacy_attachment
    with app.app_context():
        runner = app.test_cli_runner()
        result = runner.invoke(attachments_backfill_from_disk)
    assert "Backfilled 1 attachment" in result.output

    # test_cli_runner() runs the command in its OWN app context, with
    # its own commit -- this session's identity map still holds the
    # pre-backfill object, so it must be expired before re-reading or
    # the assertion below would silently check a stale cached copy
    # rather than what the command actually wrote to the database.
    db.session.expire_all()
    refreshed = db.session.get(Attachment, att.id)
    assert refreshed.file_data == content, \
        "recovered bytes must exactly match the original file"


def test_dry_run_reports_without_writing_anything(app, db,
                                                   legacy_attachment):
    att, content = legacy_attachment
    with app.app_context():
        runner = app.test_cli_runner()
        result = runner.invoke(attachments_backfill_from_disk,
                               ["--dry-run"])
    assert "Would backfill 1 attachment" in result.output

    refreshed = db.session.get(Attachment, att.id)
    assert refreshed.file_data is None, \
        "a dry run must never write file_data"


def test_backfill_is_idempotent_and_safe_to_rerun(app, db,
                                                   legacy_attachment):
    att, content = legacy_attachment
    with app.app_context():
        runner = app.test_cli_runner()
        runner.invoke(attachments_backfill_from_disk)
        # Second run: the row already has file_data, must be skipped
        # rather than re-read (and must not error if the disk file were
        # later removed).
        second = runner.invoke(attachments_backfill_from_disk)
    assert "Backfilled 0 attachment" in second.output


def test_check_reports_missing_when_no_disk_copy_and_no_file_data(
        app, db):
    att = Attachment(reference_table="vehicles", reference_id=2,
                     filename="truly_gone.jpg",
                     original_filename="Back_AVA-4017.jpg",
                     file_size=100, mime_type="image/jpeg", file_data=None)
    db.session.add(att)
    db.session.commit()

    with app.app_context():
        runner = app.test_cli_runner()
        result = runner.invoke(attachments_check)
    assert "Not found anywhere on this machine:      1" in result.output
    assert "truly_gone.jpg" not in result.output  # original_filename shown, not stored name
    assert "Back_AVA-4017.jpg" in result.output


def test_backfill_does_not_touch_already_db_backed_rows(app, db):
    """An attachment that already has file_data must never be re-read
    from disk -- confirms the 'skip if already backed' guard, not just
    that it happens to produce the right count."""
    att = Attachment(reference_table="vehicles", reference_id=3,
                     filename="already_safe.jpg",
                     original_filename="Side_AVA-4017.jpg",
                     file_size=5, mime_type="image/jpeg",
                     file_data=b"already-here")
    db.session.add(att)
    db.session.commit()

    with app.app_context():
        runner = app.test_cli_runner()
        result = runner.invoke(attachments_backfill_from_disk)
    assert "Backfilled 0 attachment" in result.output
    assert db.session.get(Attachment, att.id).file_data == b"already-here"
