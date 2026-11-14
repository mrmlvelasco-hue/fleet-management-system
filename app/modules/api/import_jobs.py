"""GET /api/v1/import-jobs/<id> -- poll an async bulk-import job's
progress. One endpoint shared by every importer (Vehicle/Driver/Vendor),
since ImportJob is generic across job_type.

Scoped to the job's own creator: this reports exactly what POST
.../import?commit=1 already told that same user it was about to do
(row counts, per-row problems), so there's no reason for a different
user to see it, and every reason not to let one.
"""
from flask import jsonify

from app.modules.api.auth import api_auth_required
from app.modules.api.routes import bp
from app.core.import_jobs.import_job_service import ImportJobService


@bp.route("/import-jobs/<int:job_id>", methods=["GET"])
@api_auth_required()
def import_job_status(api_user, job_id):
    job = ImportJobService.get(job_id)
    if job is None or job.created_by != api_user.id:
        return jsonify({"error": "not_found",
                        "message": "No import job with that id."}), 404
    return jsonify({
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "total_rows": job.total_rows,
        "processed_rows": job.processed_rows,
        "created_count": job.created_count,
        "skipped_count": job.skipped_count,
        "errors": job.errors or [],
    })
