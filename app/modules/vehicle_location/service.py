"""Vehicle location: ingest, tracking sessions, status, map, history.

THE STATUS RULE. A vehicle is LIVE only when its latest point was
CAPTURED within LOCATION_LIVE_MINUTES -- never because it merely has a
saved coordinate, and never on the time the server RECEIVED it (a phone
that was offline for three hours uploads three-hour-old points "now").

  LIVE           captured within LOCATION_LIVE_MINUTES
  LAST_REPORTED  older than that, within LOCATION_STALE_MINUTES
  STALE          older than LOCATION_STALE_MINUTES
  UNAVAILABLE    no point on record

"Sync pending" is a fifth state the app shows on its own, for points
still queued on that phone; the server cannot know about them.

SCOPE is decided by the API layer (one rule per endpoint); this service
is handed a vehicle the caller is already allowed to act on.
"""
import math
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.modules.vehicle_location.models import (
    VehicleLastLocation, VehicleLocationLog, VehicleTrackingSession)

MAX_POINTS_PER_REQUEST = 100
MAP_VEHICLE_LIMIT = 1000
FUTURE_TOLERANCE = timedelta(minutes=5)
#: A point stamped this long after its session stopped is not part of it.
SESSION_STOP_TOLERANCE = timedelta(minutes=1)
SOURCES = ("MANUAL", "AUTOMATIC")
STATUSES = ("LIVE", "LAST_REPORTED", "STALE", "UNAVAILABLE")
START_MODES = ("MANUAL", "AUTO")
MAP_PROVIDERS = ("GOOGLE", "TILES")
STOP_REASONS = ("USER", "PERMISSION_REVOKED", "GPS_DISABLED", "UNASSIGNED",
                "SIGNED_OUT", "INTERRUPTED")

DEFAULT_NOTICE = (
    "While tracking is on, FMS records this phone's location for the "
    "vehicle assigned to you, about every two minutes and whenever the "
    "vehicle moves. Fleet coordinators and authorized fleet staff can see "
    "the reported location to support fleet operations and rescue. A "
    "notification stays on screen the whole time tracking is on.")

#: (code, default) -- the code's own default applies when the row is
#: missing, so an install that has not run the seed still works.
PARAMETERS = {
    "tracking_interval_seconds": ("LOCATION_TRACKING_INTERVAL_SECONDS", 120),
    "tracking_distance_m": ("LOCATION_TRACKING_DISTANCE_M", 50),
    "stationary_heartbeat_seconds": (
        "LOCATION_STATIONARY_HEARTBEAT_SECONDS", 600),
    "max_accuracy_m": ("LOCATION_MAX_ACCURACY_M", 100),
    "live_minutes": ("LOCATION_LIVE_MINUTES", 15),
    "stale_minutes": ("LOCATION_STALE_MINUTES", 60),
    "max_backdate_hours": ("LOCATION_MAX_BACKDATE_HOURS", 72),
    "history_retention_days": ("LOCATION_HISTORY_RETENTION_DAYS", 90),
    # The map PROVIDER is a setting, not a code path the rest of the
    # feature depends on: capture, tracking, these APIs and the history
    # tables are identical whichever map draws the markers. GOOGLE uses
    # the Maps SDK for Android, whose key is set in the app's BUILD
    # configuration (never here: this endpoint is readable by every
    # signed-in user). TILES draws a raster tile server given by URL --
    # for a later move to a commercial or self-hosted tile service.
    "map_provider": ("LOCATION_MAP_PROVIDER", "GOOGLE"),
    "map_tile_url": ("LOCATION_MAP_TILE_URL", ""),
    "map_attribution": ("LOCATION_MAP_ATTRIBUTION", ""),
    "tracking_start_mode": ("LOCATION_TRACKING_START_MODE", "MANUAL"),
    "driver_can_stop_tracking": ("LOCATION_DRIVER_CAN_STOP_TRACKING", True),
    "tracking_notice_enabled": ("LOCATION_TRACKING_NOTICE_ENABLED", True),
    "tracking_notice_text": ("LOCATION_TRACKING_NOTICE_TEXT", DEFAULT_NOTICE),
}


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt):
    """Naive-UTC datetime -> '2026-11-17T10:42:18Z', or None."""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + "Z"


def _num(value):
    return float(value) if value is not None else None


class PointError(ValueError):
    """A reason one point was rejected. Shown to the user as is."""


def parse_time(value, field="captured_at"):
    """ISO-8601 WITH a time zone -> naive UTC.

    A time without a zone is refused rather than guessed: a phone set to
    Manila time would otherwise be read as UTC and land eight hours off.
    """
    if not value or not isinstance(value, str):
        raise PointError(f"{field} is required (ISO-8601 with time zone).")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise PointError(f"{field} is not a valid ISO-8601 date and time.")
    if parsed.tzinfo is None:
        raise PointError(f"{field} must include a time zone (e.g. Z).")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _uuid_of(value, field):
    try:
        return str(_uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise PointError(f"{field} must be a UUID.")


def _number(value, field, low=None, high=None, required=True):
    if value is None or value == "":
        if required:
            raise PointError(f"{field} is required.")
        return None
    if isinstance(value, bool):
        raise PointError(f"{field} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PointError(f"{field} must be a number.")
    if not math.isfinite(number):
        raise PointError(f"{field} must be a number.")
    if (low is not None and number < low) or (
            high is not None and number > high):
        raise PointError(f"{field} must be between {low} and {high}.")
    return number


class VehicleLocationService:

    # ── settings ───────────────────────────────────────────────────────

    def settings(self) -> dict:
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        params = SystemParameterService()
        out = {}
        for key, (code, default) in PARAMETERS.items():
            value = params.get(code, default=default)
            out[key] = default if value in (None, "") else value
        mode = str(out["tracking_start_mode"]).strip().upper()
        out["tracking_start_mode"] = mode if mode in START_MODES else "MANUAL"
        provider = str(out["map_provider"]).strip().upper()
        out["map_tile_url"] = str(out["map_tile_url"] or "").strip()
        out["map_attribution"] = str(out["map_attribution"] or "").strip()
        # TILES without a tile address would draw an empty map: fall back
        # to the provider the app is built with rather than show nothing.
        if provider not in MAP_PROVIDERS or (
                provider == "TILES" and not out["map_tile_url"]):
            provider = "GOOGLE"
        out["map_provider"] = provider
        for key in ("tracking_interval_seconds", "tracking_distance_m",
                    "stationary_heartbeat_seconds", "max_accuracy_m",
                    "live_minutes", "stale_minutes", "max_backdate_hours",
                    "history_retention_days"):
            try:
                out[key] = int(out[key])
            except (TypeError, ValueError):
                out[key] = PARAMETERS[key][1]
        return out

    # ── status ─────────────────────────────────────────────────────────

    @staticmethod
    def status_of(captured_at, settings, now=None) -> str:
        if captured_at is None:
            return "UNAVAILABLE"
        age = (now or utcnow()) - captured_at
        if age <= timedelta(minutes=settings["live_minutes"]):
            return "LIVE"
        if age <= timedelta(minutes=settings["stale_minutes"]):
            return "LAST_REPORTED"
        return "STALE"

    # ── ingest ─────────────────────────────────────────────────────────

    def _validate(self, point, settings, now):
        if not isinstance(point, dict):
            raise PointError("Each point must be an object.")
        clean = {"client_uuid": _uuid_of(point.get("client_uuid"),
                                         "client_uuid")}
        clean["latitude"] = _number(point.get("latitude"), "latitude",
                                    -90, 90)
        clean["longitude"] = _number(point.get("longitude"), "longitude",
                                     -180, 180)
        if abs(clean["latitude"]) < 1e-6 and abs(clean["longitude"]) < 1e-6:
            raise PointError("0,0 is not a real position (GPS had no fix).")
        accuracy = _number(point.get("accuracy_m"), "accuracy_m", 0, 100000)
        if accuracy <= 0:
            raise PointError("accuracy_m must be greater than 0.")
        clean["accuracy_m"] = accuracy
        clean["altitude_m"] = _number(point.get("altitude_m"), "altitude_m",
                                      -1000, 100000, required=False)
        clean["speed_mps"] = _number(point.get("speed_mps"), "speed_mps",
                                     0, 1000, required=False)
        clean["heading_deg"] = _number(point.get("heading_deg"),
                                       "heading_deg", 0, 360, required=False)
        source = str(point.get("source") or "").strip().upper()
        if source not in SOURCES:
            raise PointError("source must be MANUAL or AUTOMATIC.")
        clean["source"] = source
        captured = parse_time(point.get("captured_at"))
        if captured > now + FUTURE_TOLERANCE:
            raise PointError("captured_at is in the future; check the "
                             "phone's date and time.")
        limit = timedelta(hours=settings["max_backdate_hours"])
        if captured < now - limit:
            raise PointError(f"captured_at is older than "
                             f"{settings['max_backdate_hours']} hours.")
        clean["captured_at"] = captured
        clean["is_mocked"] = point.get("is_mocked") is True
        device = point.get("device_id")
        clean["device_id"] = str(device)[:64] if device else None
        if source == "AUTOMATIC":
            clean["session_uuid"] = _uuid_of(point.get("session_uuid"),
                                             "session_uuid (automatic points "
                                             "need a tracking session)")
        return clean

    def _session_for(self, clean, vehicle, user, cache):
        key = clean["session_uuid"]
        session = cache.get(key)
        if session is None:
            session = VehicleTrackingSession.query.filter_by(
                client_uuid=key).first()
        if session is None:
            # Started offline: its start call has not arrived yet.
            session = VehicleTrackingSession(
                client_uuid=key, vehicle_id=vehicle.id, user_id=user.id,
                status="ACTIVE", started_at=clean["captured_at"],
                device_id=clean["device_id"])
            db.session.add(session)
            db.session.flush()
        if session.vehicle_id != vehicle.id or session.user_id != user.id:
            raise PointError("That tracking session belongs to another "
                             "vehicle or user.")
        if session.stopped_at is not None and clean["captured_at"] > (
                session.stopped_at + SESSION_STOP_TOLERANCE):
            raise PointError("That tracking session was stopped before "
                             "this point was captured.")
        cache[key] = session
        return session

    def ingest(self, *, vehicle, user, points, allow_automatic) -> dict:
        """Store a batch of points. One result per point, in order:
        created / duplicate / rejected (with a reason).

        allow_automatic: False on the coordinator's route, which only
        ever records a manual capture.
        """
        settings = self.settings()
        now = utcnow()
        wanted = {str(p.get("client_uuid")) for p in points
                  if isinstance(p, dict) and p.get("client_uuid")}
        existing = {row.client_uuid: row for row in
                    VehicleLocationLog.query.filter(
                        VehicleLocationLog.client_uuid.in_(wanted)).all()} \
            if wanted else {}

        results, created, seen, sessions = [], [], set(), {}
        for point in points:
            ref = point.get("client_uuid") if isinstance(point, dict) else None
            try:
                clean = self._validate(point, settings, now)
                key = clean["client_uuid"]
                if key in seen:
                    results.append({"client_uuid": key,
                                    "status": "duplicate"})
                    continue
                prior = existing.get(key)
                if prior is not None:
                    if (prior.vehicle_id, prior.captured_by) != (
                            vehicle.id, user.id):
                        raise PointError("client_uuid was already used for "
                                         "another point.")
                    seen.add(key)
                    results.append({"client_uuid": key,
                                    "status": "duplicate"})
                    continue
                session = None
                if clean["source"] == "AUTOMATIC":
                    if not allow_automatic:
                        raise PointError("Only MANUAL captures can be "
                                         "recorded for a vehicle that is "
                                         "not assigned to you.")
                    session = self._session_for(clean, vehicle, user,
                                                sessions)
                row = VehicleLocationLog(
                    client_uuid=key, vehicle_id=vehicle.id,
                    latitude=clean["latitude"], longitude=clean["longitude"],
                    accuracy_m=clean["accuracy_m"],
                    altitude_m=clean["altitude_m"],
                    speed_mps=clean["speed_mps"],
                    heading_deg=clean["heading_deg"],
                    is_mocked=clean["is_mocked"], source=clean["source"],
                    captured_at=clean["captured_at"], received_at=now,
                    captured_by=user.id,
                    tracking_session_id=session.id if session else None,
                    device_id=clean["device_id"])
                db.session.add(row)
                created.append(row)
                seen.add(key)
                if session is not None and (
                        session.last_update_at is None
                        or row.captured_at > session.last_update_at):
                    session.last_update_at = row.captured_at
                results.append({"client_uuid": key, "status": "created"})
            except PointError as exc:
                results.append({"client_uuid": ref, "status": "rejected",
                                "reason": str(exc)})

        if created:
            db.session.flush()
            self._advance_latest(vehicle.id, max(
                created, key=lambda r: (r.captured_at, r.id)))
        try:
            db.session.commit()
        except IntegrityError:
            # Two uploads of the same point raced. Nothing from this
            # batch is kept; the phone retries and gets "duplicate".
            db.session.rollback()
            raise
        return {
            "results": results,
            "accepted": sum(r["status"] == "created" for r in results),
            "duplicates": sum(r["status"] == "duplicate" for r in results),
            "rejected": sum(r["status"] == "rejected" for r in results),
        }

    @staticmethod
    def _advance_latest(vehicle_id, candidate):
        pointer = db.session.get(VehicleLastLocation, vehicle_id)
        if pointer is None:
            db.session.add(VehicleLastLocation(vehicle_id=vehicle_id,
                                               log_id=candidate.id))
            return
        current = pointer.log
        if current is None or (candidate.captured_at, candidate.id) > (
                current.captured_at, current.id):
            pointer.log_id = candidate.id
            pointer.updated_at = utcnow()

    # ── tracking sessions ──────────────────────────────────────────────

    def start_session(self, *, vehicle, user, client_uuid, device_id=None,
                      started_at=None):
        key = _uuid_of(client_uuid, "client_uuid")
        session = VehicleTrackingSession.query.filter_by(
            client_uuid=key).first()
        if session is not None:
            if (session.vehicle_id, session.user_id) != (vehicle.id, user.id):
                raise PointError("client_uuid belongs to another session.")
            return session
        when = parse_time(started_at, "started_at") if started_at else utcnow()
        for other in VehicleTrackingSession.query.filter_by(
                vehicle_id=vehicle.id, user_id=user.id,
                status="ACTIVE").all():
            other.status = "STOPPED"
            other.stopped_at = when
            other.stop_reason = "REPLACED"
        session = VehicleTrackingSession(
            client_uuid=key, vehicle_id=vehicle.id, user_id=user.id,
            status="ACTIVE", started_at=when,
            device_id=(str(device_id)[:64] if device_id else None))
        db.session.add(session)
        db.session.commit()
        return session

    def stop_session(self, *, vehicle, user, client_uuid, reason=None,
                     stopped_at=None):
        """The session, stopped -- or None when this user has no such
        session on this vehicle. Stopping twice is harmless."""
        try:
            key = _uuid_of(client_uuid, "client_uuid")
        except PointError:
            return None
        session = VehicleTrackingSession.query.filter_by(
            client_uuid=key, vehicle_id=vehicle.id, user_id=user.id).first()
        if session is None:
            return None
        if session.status == "ACTIVE":
            session.status = "STOPPED"
            session.stopped_at = (parse_time(stopped_at, "stopped_at")
                                  if stopped_at else utcnow())
            reason = str(reason or "USER").strip().upper()
            session.stop_reason = reason if reason in STOP_REASONS else "USER"
            db.session.commit()
        return session

    # ── reading ────────────────────────────────────────────────────────

    @staticmethod
    def _assignees(vehicle_ids):
        from app.modules.master_data.driver.models import Driver
        from app.modules.master_data.vehicle.assignment_models import (
            VehicleAssignment)
        if not vehicle_ids:
            return {}
        rows = (db.session.query(VehicleAssignment.vehicle_id, Driver)
                .join(Driver, Driver.id == VehicleAssignment.driver_id)
                .filter(VehicleAssignment.vehicle_id.in_(vehicle_ids),
                        VehicleAssignment.assigned_to.is_(None))
                .all())
        return {vid: driver for vid, driver in rows}

    @staticmethod
    def _latest(vehicle_ids):
        if not vehicle_ids:
            return {}
        rows = (VehicleLastLocation.query
                .options(joinedload(VehicleLastLocation.log)
                         .joinedload(VehicleLocationLog.reporter))
                .filter(VehicleLastLocation.vehicle_id.in_(vehicle_ids))
                .all())
        return {r.vehicle_id: r.log for r in rows if r.log is not None}

    @staticmethod
    def _active_sessions(vehicle_ids):
        if not vehicle_ids:
            return {}
        rows = (VehicleTrackingSession.query
                .options(joinedload(VehicleTrackingSession.user))
                .filter(VehicleTrackingSession.vehicle_id.in_(vehicle_ids),
                        VehicleTrackingSession.status == "ACTIVE")
                .order_by(VehicleTrackingSession.started_at)
                .all())
        return {s.vehicle_id: s for s in rows}

    @staticmethod
    def location_dict(log):
        if log is None:
            return None
        reporter = log.reporter
        return {
            "id": log.id,
            "latitude": _num(log.latitude),
            "longitude": _num(log.longitude),
            "accuracy_m": _num(log.accuracy_m),
            "altitude_m": _num(log.altitude_m),
            "speed_mps": _num(log.speed_mps),
            "heading_deg": _num(log.heading_deg),
            "is_mocked": bool(log.is_mocked),
            "source": log.source,
            "captured_at": iso(log.captured_at),
            "received_at": iso(log.received_at),
            "captured_by": ({"id": reporter.id, "name": reporter.full_name}
                            if reporter else None),
            "tracking_session_id": log.tracking_session_id,
        }

    @staticmethod
    def session_dict(session):
        if session is None:
            return None
        return {
            "id": session.id,
            "client_uuid": session.client_uuid,
            "status": session.status,
            "started_at": iso(session.started_at),
            "stopped_at": iso(session.stopped_at),
            "stop_reason": session.stop_reason,
            "last_update_at": iso(session.last_update_at),
            "user": ({"id": session.user.id, "name": session.user.full_name}
                     if session.user else None),
        }

    @staticmethod
    def vehicle_dict(vehicle, driver, include_phone):
        from app.core.company_resolution import (
            company_display, effective_company)
        assignee = None
        if driver is not None:
            assignee = {"id": driver.id, "name": driver.full_name,
                        "employee_number": driver.employee_number}
            if include_phone:
                assignee["phone"] = driver.phone
        return {
            "id": vehicle.id,
            "plate_number": vehicle.plate_number,
            "conduction_number": vehicle.conduction_number,
            "brand": vehicle.brand,
            "model": vehicle.model,
            "year": vehicle.year,
            "color": getattr(vehicle, "color", None),
            "vehicle_type": (vehicle.vehicle_type.name
                             if vehicle.vehicle_type else None),
            "company": company_display(effective_company(vehicle)),
            "branch": vehicle.branch.name if vehicle.branch else None,
            "status": vehicle.status,
            "assignee": assignee,
        }

    def map_rows(self, vehicles, *, status=None, include_phone=False):
        settings = self.settings()
        ids = [v.id for v in vehicles]
        latest = self._latest(ids)
        sessions = self._active_sessions(ids)
        assignees = self._assignees(ids)
        now = utcnow()
        rows, counts = [], {"ALL": 0, **{s: 0 for s in STATUSES}}
        for v in vehicles:
            log = latest.get(v.id)
            state = self.status_of(log.captured_at if log else None,
                                   settings, now)
            counts["ALL"] += 1
            counts[state] += 1
            if status and state != status:
                continue
            rows.append({
                **self.vehicle_dict(v, assignees.get(v.id), include_phone),
                "location_status": state,
                "status": state,
                "vehicle_status": v.status,
                "location": self.location_dict(log),
                "tracking": self.session_dict(sessions.get(v.id)),
            })
        return rows, counts

    def detail(self, vehicle, *, include_phone):
        rows, _counts = self.map_rows([vehicle], include_phone=include_phone)
        row = rows[0]
        return {"vehicle": {k: row[k] for k in (
                    "id", "plate_number", "conduction_number", "brand",
                    "model", "year", "color", "vehicle_type", "company",
                    "branch", "assignee")},
                "vehicle_status": row["vehicle_status"],
                "status": row["location_status"],
                "location": row["location"],
                "tracking": row["tracking"],
                "settings": {k: self.settings()[k] for k in (
                    "live_minutes", "stale_minutes")}}

    def history(self, vehicle, *, source=None, date_from=None, date_to=None,
                page=1, per_page=50):
        query = (VehicleLocationLog.query
                 .options(joinedload(VehicleLocationLog.reporter))
                 .filter(VehicleLocationLog.vehicle_id == vehicle.id))
        if source:
            query = query.filter(VehicleLocationLog.source == source)
        if date_from is not None:
            query = query.filter(VehicleLocationLog.captured_at >= date_from)
        if date_to is not None:
            query = query.filter(VehicleLocationLog.captured_at <= date_to)
        total = query.count()
        rows = (query.order_by(VehicleLocationLog.captured_at.desc(),
                               VehicleLocationLog.id.desc())
                .offset((page - 1) * per_page).limit(per_page).all())
        return [self.location_dict(r) for r in rows], total

    # ── retention ──────────────────────────────────────────────────────

    def purge(self, retention_days=None, dry_run=False) -> int:
        """Delete points captured before the retention period. Each
        vehicle's latest point is always kept, so the map can still
        say where a vehicle was last reported. Returns the count."""
        days = (retention_days if retention_days is not None
                else self.settings()["history_retention_days"])
        cutoff = utcnow() - timedelta(days=int(days))
        keep = [r.log_id for r in VehicleLastLocation.query.all()]
        query = VehicleLocationLog.query.filter(
            VehicleLocationLog.captured_at < cutoff)
        if keep:
            query = query.filter(~VehicleLocationLog.id.in_(keep))
        count = query.count()
        if dry_run or not count:
            return count
        query.delete(synchronize_session=False)
        still_used = db.session.query(
            VehicleLocationLog.tracking_session_id).filter(
            VehicleLocationLog.tracking_session_id.isnot(None))
        (VehicleTrackingSession.query
         .filter(VehicleTrackingSession.status == "STOPPED",
                 VehicleTrackingSession.stopped_at < cutoff,
                 ~VehicleTrackingSession.id.in_(still_used))
         .delete(synchronize_session=False))
        db.session.commit()
        return count
