"""Repositories for approval configuration."""
from app.core.repository.base_repository import BaseRepository
from app.modules.approval_config.models import (
    ApprovalPath, ApprovalLevel, ApprovalMatrix)


class ApprovalPathRepository(BaseRepository):
    model = ApprovalPath


class ApprovalLevelRepository(BaseRepository):
    model = ApprovalLevel


class ApprovalMatrixRepository(BaseRepository):
    model = ApprovalMatrix

    def list_for_document_type(self, document_type_id: int):
        return (ApprovalMatrix.query
                .filter_by(document_type_id=document_type_id, is_active=True)
                .all())
