"""Business rules for Document Type and Numbering Scheme maintenance."""
from app.extensions import db
from app.modules.document_config.models import NumberingScheme
from app.modules.document_config.repository import (
    DocumentTypeRepository, NumberingSchemeRepository)


class DuplicateDocumentTypeError(Exception):
    pass


class DuplicateSchemeError(Exception):
    pass


class DocumentTypeService:
    def __init__(self):
        self.repo = DocumentTypeRepository()

    def create(self, code, name, description=None, requires_approval=False,
               auto_numbering=False, printable=False, mobile_available=False,
               attachment_allowed=False):
        if self.repo.get_by_code(code) is not None:
            raise DuplicateDocumentTypeError(
                f"Document type code '{code}' already exists.")
        dt = self.repo.create(
            code=code, name=name, description=description,
            requires_approval=requires_approval, auto_numbering=auto_numbering,
            printable=printable, mobile_available=mobile_available,
            attachment_allowed=attachment_allowed)
        db.session.commit()
        return dt

    def update(self, document_type_id, **kwargs):
        dt = self.repo.update(document_type_id, **kwargs)
        db.session.commit()
        return dt

    def deactivate(self, document_type_id):
        self.repo.soft_delete(document_type_id)
        db.session.commit()


class NumberingSchemeService:
    def __init__(self):
        self.repo = NumberingSchemeRepository()

    def create(self, document_type_id, **kwargs):
        if self.repo.get_for_document_type(document_type_id) is not None:
            raise DuplicateSchemeError(
                "A numbering scheme already exists for this document type.")
        scheme = self.repo.create(document_type_id=document_type_id, **kwargs)
        db.session.commit()
        return scheme

    def update(self, scheme_id, **kwargs):
        scheme = self.repo.update(scheme_id, **kwargs)
        db.session.commit()
        return scheme

    def deactivate(self, scheme_id):
        self.repo.soft_delete(scheme_id)
        db.session.commit()

    @staticmethod
    def preview(scheme: NumberingScheme, sample_number: int = 1,
                year: int = 2026, month: int = 1) -> str:
        from app.core.numbering.numbering_service import format_number
        return format_number(scheme, sample_number, year, month)
