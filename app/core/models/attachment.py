"""Generic file attachment model.

Every master and transaction module shares this via reference_table +
reference_id — the same pattern used by AuditLog and ApprovalInstance.
No per-module attachment tables needed.
"""
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
