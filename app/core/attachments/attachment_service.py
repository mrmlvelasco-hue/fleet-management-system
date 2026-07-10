"""Generic attachment service — upload, list, delete for any module.

Allowed extensions and max file size are read from SystemParameters
(ATTACHMENT_ALLOWED_EXTENSIONS, ATTACHMENT_MAX_SIZE_MB) so they are
configurable without code changes.
"""
import os
import uuid
from werkzeug.utils import secure_filename

from app.extensions import db
from app.core.models.attachment import Attachment

DEFAULT_ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx",
                               "jpg", "jpeg", "png", "gif"}
DEFAULT_MAX_MB = 10


def _get_upload_dir(reference_table: str) -> str:
    base = os.path.join("instance", "uploads", reference_table)
    os.makedirs(base, exist_ok=True)
    return base


def _allowed(filename: str, allowed: set) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


class AttachmentError(Exception):
    pass


class AttachmentService:
    def __init__(self):
        try:
            from app.modules.system_admin.services.system_parameter_service import (
                SystemParameterService)
            svc = SystemParameterService()
            exts = svc.get("ATTACHMENT_ALLOWED_EXTENSIONS")
            self._allowed = (set(exts.split(",")) if exts
                             else DEFAULT_ALLOWED_EXTENSIONS)
            max_mb = svc.get("ATTACHMENT_MAX_SIZE_MB",
                             default=DEFAULT_MAX_MB)
            self._max_bytes = int(max_mb) * 1024 * 1024
        except Exception:
            self._allowed = DEFAULT_ALLOWED_EXTENSIONS
            self._max_bytes = DEFAULT_MAX_MB * 1024 * 1024

    def upload(self, file, reference_table: str, reference_id: int,
               user=None) -> Attachment:
        """Save a file and create an Attachment row. Raises AttachmentError."""
        if not file or not file.filename:
            raise AttachmentError("No file provided.")
        if not _allowed(file.filename, self._allowed):
            raise AttachmentError(
                f"File type not allowed. Permitted: "
                f"{', '.join(sorted(self._allowed))}")
        content = file.read()
        if len(content) > self._max_bytes:
            raise AttachmentError(
                f"File exceeds maximum size of "
                f"{self._max_bytes // (1024*1024)} MB.")
        file.seek(0)
        original = secure_filename(file.filename)
        ext = original.rsplit(".", 1)[1].lower()
        stored = f"{uuid.uuid4().hex}.{ext}"
        dest = os.path.join(_get_upload_dir(reference_table), stored)
        file.save(dest)
        att = Attachment(
            reference_table=reference_table,
            reference_id=reference_id,
            filename=stored,
            original_filename=original,
            file_size=len(content),
            mime_type=file.content_type,
            uploaded_by=user.id if user else None)
        db.session.add(att)
        db.session.commit()
        return att

    def list_for(self, reference_table: str,
                 reference_id: int) -> list:
        return (Attachment.query
                .filter_by(reference_table=reference_table,
                           reference_id=reference_id,
                           is_active=True)
                .order_by(Attachment.id.desc())
                .all())

    def delete(self, attachment_id: int, user=None) -> None:
        att = db.session.get(Attachment, attachment_id)
        if att:
            att.is_active = False
            db.session.commit()
