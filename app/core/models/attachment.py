"""Generic file attachment model.

Every master and transaction module shares this via reference_table +
reference_id — the same pattern used by AuditLog and ApprovalInstance.
No per-module attachment tables needed.

file_data stores the actual file bytes IN THE SHARED DATABASE (not just
on whatever machine's local disk happened to handle the upload). This
matters specifically because this app is developed against a single
shared MySQL server (10.10.160.51) from multiple separate machines
(e.g. a developer's personal laptop and their office laptop) — the
Attachment ROW was always visible from both, since the database is
centralized, but the file itself previously lived only in whichever
machine's local `instance/uploads/` folder handled that particular
upload. Storing the bytes in the same shared database the row lives in
makes the file itself just as centrally accessible as its metadata.
"""
from sqlalchemy.dialects.mysql import LONGBLOB

from app.extensions import db
from app.core.models.base import BaseModel


class Attachment(db.Model, BaseModel):
    __tablename__ = "attachments"
    reference_table = db.Column(db.String(100), nullable=False, index=True)
    reference_id = db.Column(db.Integer, nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)        # stored name
    original_filename = db.Column(db.String(255), nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)  # bytes
    mime_type = db.Column(db.String(100))
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                            nullable=True)
    uploader = db.relationship("User")
    # Portable across SQLite (dev/test) and MySQL (prod): plain
    # LargeBinary on SQLite (no practical size limit), explicit LONGBLOB
    # on MySQL (plain BLOB there defaults to a 64KB cap, nowhere near
    # enough for a real photo). Nullable so attachments uploaded BEFORE
    # this column existed keep working via the on-disk fallback in
    # AttachmentService/the serving routes — nothing already uploaded is
    # lost or needs to be re-uploaded.
    file_data = db.Column(
        db.LargeBinary().with_variant(LONGBLOB, "mysql"), nullable=True)
