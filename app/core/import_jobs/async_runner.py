"""Runs one bulk-import function on a background thread, reporting
progress through ImportJobService as it goes.

Shared by the Vehicle/Driver/Vendor import endpoints -- the POST handler
reads the uploaded file into memory, creates the ImportJob row, calls
start_import_job(), and returns {job_id} immediately (202). The thread
gets its own Flask app-context and its own DB session (never the
request's session, which is gone the moment the request returns), and
its own fresh in-memory copy of the file bytes (a request's FileStorage
stream is tied to the request/WSGI lifecycle and is not safe to read
from a thread that outlives the request).
"""
import threading
from io import BytesIO

from app.extensions import db
from app.core.import_jobs.import_job_service import ImportJobService


def start_import_job(app, job_id, import_fn, file_bytes, **import_kwargs):
    """Start import_fn(BytesIO(file_bytes), dry_run=False,
    progress_callback=..., **import_kwargs) on a background thread.

    Returns the Thread (daemon=True) -- callers don't join it; the
    frontend learns completion by polling GET /api/import-jobs/<id>.
    """
    def _run():
        with app.app_context():
            ImportJobService.mark_running(job_id)
            db.session.commit()

            def _on_progress(processed_rows):
                ImportJobService.update_progress(job_id, processed_rows)
                db.session.commit()

            try:
                stats = import_fn(BytesIO(file_bytes), dry_run=False,
                                   progress_callback=_on_progress,
                                   **import_kwargs)
                ImportJobService.complete(
                    job_id, created_count=stats["created"],
                    skipped_count=stats["skipped"], errors=stats["errors"],
                    total_rows=stats["total_rows"])
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                ImportJobService.fail(job_id, str(exc))
                db.session.commit()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
