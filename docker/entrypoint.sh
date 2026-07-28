#!/usr/bin/env bash
# ---------------------------------------------------------------------
# Container entrypoint.
#
# One image, three roles (web / scheduler / shell) selected by the
# command, so the application code can never drift between them.
# ---------------------------------------------------------------------
set -euo pipefail

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-3306}"

wait_for_db() {
  echo "Waiting for MySQL at ${DB_HOST}:${DB_PORT}..."
  # MySQL accepts TCP connections a while before it is ready to serve
  # queries, so a plain port check is not enough on a cold start --
  # migrations would fail on a database still initialising.
  for _ in $(seq 1 60); do
    if nc -z "${DB_HOST}" "${DB_PORT}" 2>/dev/null; then
      echo "MySQL is accepting connections."
      sleep 2
      return 0
    fi
    sleep 2
  done
  echo "ERROR: MySQL did not become available in time." >&2
  exit 1
}

case "${1:-web}" in
  web)
    wait_for_db
    echo "Applying database migrations..."
    flask db upgrade
    # Seeding is idempotent, so it is safe on every start and keeps a
    # newly-added permission or lookup from being silently missing after
    # an upgrade.
    if [ -n "${FMS_ADMIN_PASSWORD:-}" ]; then
      echo "Seeding reference data..."
      flask seed all --admin-password "${FMS_ADMIN_PASSWORD}"
    else
      echo "FMS_ADMIN_PASSWORD not set — skipping seed."
    fi
    echo "Starting Gunicorn..."
    # Threads rather than many processes: this workload is I/O-bound
    # (waiting on MySQL), and each extra process would open its own
    # connection pool against MySQL's connection limit.
    exec gunicorn --bind 0.0.0.0:8000 \
         --workers "${GUNICORN_WORKERS:-3}" \
         --threads "${GUNICORN_THREADS:-4}" \
         --timeout 120 \
         --access-logfile - --error-logfile - \
         wsgi:app
    ;;

  scheduler)
    wait_for_db
    echo "Starting scheduler loop (email outbox drain)..."
    # A plain loop rather than Celery beat: the recurring work here is a
    # single idempotent command that returns immediately when there is
    # nothing to do, so a broker, a worker and a beat process would be
    # three more moving parts for no benefit.
    while true; do
      flask email send-pending || echo "email send-pending failed; will retry."
      sleep "${SCHEDULER_INTERVAL_SECONDS:-60}"
    done
    ;;

  shell)
    wait_for_db
    exec flask shell
    ;;

  *)
    exec "$@"
    ;;
esac
