"""BaseModel mixin: shared audit columns and soft-delete flag.

Every FMS table inherits this so audit columns are uniform and hard
deletes are avoided (Master Data must retain full history).
"""
from datetime import datetime, timezone

from app.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class BaseModel:
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    created_by = db.Column(db.Integer, nullable=True)  # user id; FK omitted to avoid circular deps
    updated_by = db.Column(db.Integer, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
