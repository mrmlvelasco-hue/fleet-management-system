"""ImportJob: one row per bulk Excel import run (Vehicle/Driver/Vendor).

Generic across job_type so every importer shares this one table/service
instead of three copies -- the same "reuse the generic engine" pattern
as AuditLog, the Approval Engine, etc.

A real percentage during import requires the backend to report progress
incrementally rather than in one blocking request/response. This row is
updated by a background thread as it processes each row, and polled by
the frontend via GET /api/import-jobs/<id> -- a DB-backed status is the
only option of the ones considered that stays correct no matter how many
app worker processes are running, since an in-memory tracker is invisible
across workers.
"""
from datetime import datetime, timezone

from app.extensions import db


class ImportJob(db.Model):
    __tablename__ = "import_jobs"

    id = db.Column(db.Integer, primary_key=True)
    job_type = db.Column(db.String(20), nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False, default="queued")
    total_rows = db.Column(db.Integer, nullable=False, default=0)
    processed_rows = db.Column(db.Integer, nullable=False, default=0)
    created_count = db.Column(db.Integer, nullable=True)
    skipped_count = db.Column(db.Integer, nullable=True)
    errors = db.Column(db.JSON, nullable=True)
    created_by = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime,
                            default=lambda: datetime.now(timezone.utc),
                            nullable=False)
    updated_at = db.Column(db.DateTime,
                            default=lambda: datetime.now(timezone.utc),
                            onupdate=lambda: datetime.now(timezone.utc),
                            nullable=False)
