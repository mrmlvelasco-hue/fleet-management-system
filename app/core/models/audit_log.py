"""AuditLog: one row per insert/update/delete on any audited model."""
from datetime import datetime, timezone

from app.extensions import db


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    table_name = db.Column(db.String(100), nullable=False, index=True)
    record_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(10), nullable=False)  # CREATE/UPDATE/DELETE
    old_values = db.Column(db.JSON, nullable=True)
    new_values = db.Column(db.JSON, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)
    timestamp = db.Column(db.DateTime,
                          default=lambda: datetime.now(timezone.utc),
                          nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
