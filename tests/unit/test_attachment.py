import io
import pytest
from app.core.attachments.attachment_service import (
    AttachmentService, AttachmentError)
from app.core.models.attachment import Attachment


class FakeFile:
    def __init__(self, name, content=b"data", content_type="application/pdf"):
        self.filename = name
        self.content_type = content_type
        self._content = content
        self._pos = 0

    def read(self): return self._content
    def seek(self, pos): self._pos = pos
    def save(self, path):
        with open(path, "wb") as f:
            f.write(self._content)


def test_upload_creates_attachment(db):
    svc = AttachmentService()
    f = FakeFile("report.pdf")
    att = svc.upload(f, "vehicles", 1)
    assert att.id is not None
    assert att.original_filename == "report.pdf"
    assert att.reference_table == "vehicles"


def test_disallowed_extension_rejected(db):
    svc = AttachmentService()
    with pytest.raises(AttachmentError, match="not allowed"):
        svc.upload(FakeFile("script.exe"), "vehicles", 1)


def test_list_returns_active_only(db):
    svc = AttachmentService()
    a1 = svc.upload(FakeFile("a.pdf"), "vehicles", 1)
    a2 = svc.upload(FakeFile("b.pdf"), "vehicles", 1)
    svc.delete(a2.id)
    result = svc.list_for("vehicles", 1)
    assert len(result) == 1
    assert result[0].id == a1.id


def test_delete_soft_deletes(db):
    svc = AttachmentService()
    att = svc.upload(FakeFile("c.pdf"), "vehicles", 2)
    svc.delete(att.id)
    assert db.session.get(Attachment, att.id).is_active is False
