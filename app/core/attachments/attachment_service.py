"""Generic attachment service — upload, list, delete for any module.

Allowed extensions and max file size are read from SystemParameters
(ATTACHMENT_ALLOWED_EXTENSIONS, ATTACHMENT_MAX_SIZE_MB) so they are
configurable without code changes.
"""
import os
import uuid
from werkzeug.utils import secure_filename
from flask import current_app

from app.extensions import db
from app.core.models.attachment import Attachment

DEFAULT_ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx",
                               "jpg", "jpeg", "png", "gif"}
DEFAULT_MAX_MB = 10


def _get_upload_dir(reference_table: str) -> str:
    # Use Flask's absolute instance_path (not a CWD-relative path) so
    # uploads are found consistently regardless of process working
    # directory (dev server, gunicorn, tests, etc.)
    base = os.path.join(current_app.instance_path, "uploads", reference_table)
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
               user=None, document_type=None) -> Attachment:
        """Save a file and create an Attachment row. Raises AttachmentError.

        The file's bytes are stored directly in the `file_data` column of
        the SAME shared database this row lives in — this is the
        authoritative copy, readable from any machine connecting to that
        database (e.g. a personal laptop AND an office laptop both
        pointed at the same MySQL server), unlike the local disk copy
        below, which only exists on whichever machine happened to handle
        this particular upload.

        Still ALSO writes to local disk as a redundant backup copy (not
        the primary source of truth) — belt-and-suspenders in case of a
        future storage-strategy change, at negligible extra cost since
        the bytes are already in memory. Serving always prefers
        `file_data` over the disk copy when both exist (see
        attachment_view/attachment_download in master_data/routes.py).
        """
        if not file or not file.filename:
            raise AttachmentError("No file provided.")
        # Validated against Lookup Maintenance rather than trusted: the
        # value arrives from a form field, so anything can be posted.
        # An empty selection is legitimate (the type is optional) and is
        # normalised to None rather than stored as "".
        document_type = (document_type or "").strip() or None
        if document_type is not None \
                and not self.is_valid_document_type(document_type):
            raise AttachmentError(
                "That is not a document type this system recognises.")
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
        try:
            dest = os.path.join(_get_upload_dir(reference_table), stored)
            file.save(dest)
        except OSError:
            # The local-disk backup copy is optional -- if this machine's
            # disk isn't writable for some reason, that must never block
            # the upload, since the database copy (below) is already the
            # authoritative one.
            pass
        att = Attachment(
            reference_table=reference_table,
            reference_id=reference_id,
            filename=stored,
            original_filename=original,
            file_size=len(content),
            mime_type=file.content_type,
            file_data=content,
            document_type=document_type,
            uploaded_by=user.id if user else None)
        db.session.add(att)
        db.session.commit()
        return att

    DOCUMENT_TYPE_LOOKUP = "ATTACHMENT_DOC_TYPE"

    def document_types(self):
        """The document types on offer, from Lookup Maintenance."""
        from app.modules.system_admin.services.lookup_service import (
            LookupService)
        return LookupService().get_by_type_with_fallback(
            self.DOCUMENT_TYPE_LOOKUP)

    def is_valid_document_type(self, code) -> bool:
        return any(row.code == code for row in self.document_types())

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
