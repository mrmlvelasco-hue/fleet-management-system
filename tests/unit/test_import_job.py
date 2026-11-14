"""ImportJob: generic progress-tracking row shared by every bulk Excel
importer (Vehicle/Driver/Vendor), so the frontend can poll one endpoint
shape no matter which import is running.
"""
import pytest

from app.core.models.import_job import ImportJob
from app.core.import_jobs.import_job_service import ImportJobService


def test_create_starts_a_job_queued_with_zero_progress(db):
    job = ImportJobService.create(job_type="driver", total_rows=184,
                                   created_by=None)
    db.session.commit()
    fetched = ImportJob.query.get(job.id)
    assert fetched.job_type == "driver"
    assert fetched.status == "queued"
    assert fetched.total_rows == 184
    assert fetched.processed_rows == 0


def test_mark_running_updates_status(db):
    job = ImportJobService.create(job_type="vehicle", total_rows=10,
                                   created_by=None)
    db.session.commit()
    ImportJobService.mark_running(job.id)
    db.session.commit()
    assert ImportJob.query.get(job.id).status == "running"


def test_update_progress_sets_processed_rows(db):
    job = ImportJobService.create(job_type="vendor", total_rows=50,
                                   created_by=None)
    db.session.commit()
    ImportJobService.update_progress(job.id, processed_rows=17)
    db.session.commit()
    assert ImportJob.query.get(job.id).processed_rows == 17


def test_complete_sets_status_done_and_final_counts(db):
    job = ImportJobService.create(job_type="driver", total_rows=184,
                                   created_by=None)
    db.session.commit()
    ImportJobService.complete(job.id, created_count=184, skipped_count=0,
                               errors=[])
    db.session.commit()
    fetched = ImportJob.query.get(job.id)
    assert fetched.status == "done"
    assert fetched.created_count == 184


def test_complete_corrects_total_rows_to_what_the_import_actually_counted(db):
    # The client's guess at total_rows (from its own prior dry-run) is
    # only ever a progress-bar denominator while the job runs -- the
    # import's own count, once it finishes, is the one source of truth
    # callers should see in the final status.
    job = ImportJobService.create(job_type="vendor", total_rows=0,
                                   created_by=None)
    db.session.commit()
    ImportJobService.complete(job.id, created_count=1, skipped_count=6,
                               errors=[], total_rows=7)
    db.session.commit()
    fetched = ImportJob.query.get(job.id)
    assert fetched.total_rows == 7
    assert fetched.processed_rows == 7


def test_fail_sets_status_failed_with_message(db):
    job = ImportJobService.create(job_type="driver", total_rows=5,
                                   created_by=None)
    db.session.commit()
    ImportJobService.fail(job.id, "Could not read the file: corrupt zip")
    db.session.commit()
    fetched = ImportJob.query.get(job.id)
    assert fetched.status == "failed"
    assert "corrupt zip" in fetched.errors[0]["problems"][0]


def test_get_returns_none_for_unknown_job(db):
    assert ImportJobService.get(999999) is None
