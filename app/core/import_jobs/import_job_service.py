"""ImportJobService: the only thing that touches ImportJob rows.

Kept deliberately tiny -- plain status transitions, no business rules.
The actual import logic (import_drivers/import_vehicles/import_vendors)
never writes to ImportJob directly; it calls back through
update_progress() so this service stays the single place that knows
the table's shape.
"""
from app.extensions import db
from app.core.models.import_job import ImportJob


class ImportJobService:

    @staticmethod
    def create(job_type, total_rows, created_by):
        job = ImportJob(job_type=job_type, status="queued",
                         total_rows=total_rows, processed_rows=0,
                         created_by=created_by)
        db.session.add(job)
        db.session.flush()
        return job

    @staticmethod
    def mark_running(job_id):
        job = ImportJob.query.get(job_id)
        if job is not None:
            job.status = "running"

    @staticmethod
    def update_progress(job_id, processed_rows):
        job = ImportJob.query.get(job_id)
        if job is not None:
            job.processed_rows = processed_rows

    @staticmethod
    def complete(job_id, created_count, skipped_count, errors,
                 total_rows=None):
        job = ImportJob.query.get(job_id)
        if job is not None:
            job.status = "done"
            job.created_count = created_count
            job.skipped_count = skipped_count
            job.errors = errors
            # total_rows at creation was only ever the client's own
            # prior-dry-run guess, used as the progress bar's
            # denominator while running. The import's own count, once
            # it actually finishes, is the one source of truth.
            if total_rows is not None:
                job.total_rows = total_rows
            job.processed_rows = job.total_rows

    @staticmethod
    def fail(job_id, message):
        job = ImportJob.query.get(job_id)
        if job is not None:
            job.status = "failed"
            job.errors = [{"row": None, "identifier": None,
                           "problems": [message]}]

    @staticmethod
    def get(job_id):
        return ImportJob.query.get(job_id)
