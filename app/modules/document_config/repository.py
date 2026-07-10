"""Repositories for document configuration."""
from app.core.repository.base_repository import BaseRepository
from app.modules.document_config.models import (
    DocumentType, NumberingScheme, NumberingCounter)


class DocumentTypeRepository(BaseRepository):
    model = DocumentType

    def get_by_code(self, code: str):
        return DocumentType.query.filter_by(code=code, is_active=True).first()


class NumberingSchemeRepository(BaseRepository):
    model = NumberingScheme

    def get_for_document_type(self, document_type_id: int):
        return NumberingScheme.query.filter_by(
            document_type_id=document_type_id, is_active=True).first()


class NumberingCounterRepository(BaseRepository):
    model = NumberingCounter
