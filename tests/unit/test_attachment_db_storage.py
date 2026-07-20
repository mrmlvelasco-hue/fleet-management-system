"""Tests for shared-database attachment storage (Attachment.file_data)
and the audit-service fix that made it possible.

Context: this app is developed against a single shared MySQL server from
multiple separate machines (e.g. a developer's personal laptop and
office laptop). Before file_data existed, the Attachment ROW was always
visible from both (centralized database), but the actual file bytes only
existed on whichever machine's local disk happened to handle the
upload -- so a photo uploaded from one machine silently failed to
display when viewed from the other. Storing bytes in the same shared
database the row lives in fixes this.
"""
from io import BytesIO

import pytest

from app.core.attachments.attachment_service import AttachmentService
from app.core.models.attachment import Attachment


class _FakeFile:
    """Minimal werkzeug FileStorage stand-in for unit testing without a
    real HTTP request."""
    def __init__(self, filename, content, content_type="image/png"):
        self.filename = filename
        self.content_type = content_type
        self._buf = BytesIO(content)

    def read(self):
        return self._buf.read()

    def seek(self, pos):
        self._buf.seek(pos)

    def save(self, dest):
        with open(dest, "wb") as f:
            f.write(self._buf.getvalue())
        self._buf.seek(0)


PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a4944415478da6360000002000155ba62a30000000049454e44ae426082")


def test_upload_stores_bytes_in_the_database(db, app):
    with app.app_context():
        att = AttachmentService().upload(
            _FakeFile("cr_photo.png", PNG_BYTES), "vehicles", 1)
        assert att.file_data == PNG_BYTES
        assert att.file_size == len(PNG_BYTES)


def test_attachment_readable_even_if_local_disk_copy_is_missing(db, app, tmp_path):
    """The exact scenario reported: a file viewed from a machine that
    never had it on local disk (e.g. uploaded from a personal laptop,
    viewed from an office laptop) must still work, because the
    authoritative copy lives in the shared database, not on disk."""
    with app.app_context():
        att = AttachmentService().upload(
            _FakeFile("front.png", PNG_BYTES), "vehicles", 1)
        att_id = att.id

    # Simulate "this machine never received the file" by wiping whatever
    # local disk copy this test run happened to create.
    import os
    with app.app_context():
        from app.extensions import db as _db
        refreshed = _db.session.get(Attachment, att_id)
        disk_path = os.path.join(app.instance_path, "uploads", "vehicles",
                                 refreshed.filename)
        if os.path.exists(disk_path):
            os.remove(disk_path)
        assert not os.path.exists(disk_path)
        # The database copy alone must still be sufficient.
        assert refreshed.file_data == PNG_BYTES


def test_audit_log_does_not_crash_on_binary_column(db, app):
    """Regression test for a real bug hit while building this: the
    generic AuditService JSON-serializes every column automatically, and
    without special handling, raw bytes (file_data) crashed the flush
    with 'Object of type bytes is not JSON serializable' on every single
    attachment upload."""
    with app.app_context():
        # Must not raise.
        att = AttachmentService().upload(
            _FakeFile("back.png", PNG_BYTES), "vehicles", 1)
        assert att.id is not None

        from app.core.models.audit_log import AuditLog
        log = (AuditLog.query.filter_by(table_name="attachments",
                                        action="CREATE")
              .order_by(AuditLog.id.desc()).first())
        assert log is not None
        # The binary value must be represented as a safe placeholder, not
        # the raw bytes themselves.
        assert "binary data" in str(log.new_values.get("file_data", ""))
