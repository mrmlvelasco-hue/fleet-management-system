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
    echo "Starting scheduler loop (email outbox drain + daily scans)..."
    # A plain loop rather than Celery beat: the recurring work here is a
    # handful of idempotent commands that return immediately when there
    # is nothing to do, so a broker, a worker and a beat process would be
    # three more moving parts for no benefit.
    #
    # Two cadences in one loop. The outbox drains every tick, because a
    # queued mail waiting a minute is already the longest anyone should
    # wait. The daily scans run once per calendar day: they only move
    # records across a date boundary, so a second run the same day finds
    # nothing and is pure load.
    #
    # The marker is a plain variable, so a container restart runs the
    # daily work once on start. That is deliberate -- the commands are
    # idempotent, and a scheduler that has been down over midnight
    # should catch up immediately rather than wait for the next one.
    last_daily_run=""
    while true; do
      flask email send-pending || echo "email send-pending failed; will retry."

      today="$(date +%F)"
      if [ "${last_daily_run}" != "${today}" ]; then
        daily_ok=1

        # Preventive Maintenance: due/overdue scan -- fires the
        # due_soon/overdue notifications and creates Maintenance Orders
        # for AUTO_MO-policy templates. Without this nothing ever
        # noticed a vehicle coming due; the command existed and was
        # expected to be run by hand.
        flask pm run-due-check || { daily_ok=0
          echo "pm run-due-check failed; will retry on the next tick."; }

        # Vehicle Registration: the same scan for LTO renewals --
        # notifications, plus DRAFT renewals for AUTO_REGISTRATION
        # templates.
        flask registration run-due-check || { daily_ok=0
          echo "registration run-due-check failed; will retry on the next tick."; }

        # Authority To Drive: stamp permits whose Valid To has passed as
        # EXPIRED, so a lapsed permit stops reading "APPROVED & ACTIVE"
        # in the lists (KI-10). Editing a lapsed permit is already
        # refused by its DATE whether or not this has run, so a failure
        # here is a stale label, never an open door.
        flask atd run-expiry-check || { daily_ok=0
          echo "atd run-expiry-check failed; will retry on the next tick."; }

        # The marker only advances when the whole day's work succeeded,
        # so a transient database blip retries on the next tick rather
        # than skipping a day. Every one of these is idempotent, so the
        # ones that already succeeded cost nothing on the retry.
        if [ "${daily_ok}" = "1" ]; then
          last_daily_run="${today}"
        fi
      fi

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
