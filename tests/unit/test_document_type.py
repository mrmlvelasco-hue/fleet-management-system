import pytest

from app.modules.document_config.service import (
    DocumentTypeService, DuplicateDocumentTypeError)
from app.modules.document_config.models import DocumentType


def test_create_document_type(db):
    svc = DocumentTypeService()
    dt = svc.create(code="TT", name="Trip Ticket", requires_approval=True,
                    auto_numbering=True, printable=True)
    assert dt.id is not None
    assert dt.code == "TT"
    assert dt.mobile_available is False
    assert dt.attachment_allowed is False


def test_duplicate_code_rejected(db):
    svc = DocumentTypeService()
    svc.create(code="MO", name="Maintenance Order")
    with pytest.raises(DuplicateDocumentTypeError):
        svc.create(code="MO", name="Other")


def test_update_flags(db):
    svc = DocumentTypeService()
    dt = svc.create(code="PR", name="Purchase Request")
    svc.update(dt.id, printable=True, attachment_allowed=True)
    assert db.session.get(DocumentType, dt.id).printable is True
