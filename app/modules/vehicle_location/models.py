"""Location history, tracking sessions and the latest point per vehicle.

Kept apart from the Vehicle master on purpose: a location is an event
with a time, a reporter and an accuracy, not an attribute of the
vehicle, and the history grows by hundreds of rows per vehicle per day.

Times are stored as naive UTC, like every other DateTime in FMS.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class VehicleTrackingSession(db.Model):
    """One run of automatic tracking, started and stopped on the phone.

    `client_uuid` is generated on the phone, so a session started while
    offline is the same session when its start call and its points
    arrive later, in any order.
    """
    __tablename__ = "vehicle_tracking_sessions"

    id = db.Column(db.Integer, primary_key=True)
    client_uuid = db.Column(db.String(36), unique=True, nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                        nullable=False)
    status = db.Column(db.String(10), nullable=False, default="ACTIVE")
    started_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    stopped_at = db.Column(db.DateTime, nullable=True)
    #: USER, REPLACED, PERMISSION_REVOKED, GPS_DISABLED, UNASSIGNED,
    #: SIGNED_OUT, INTERRUPTED -- free text from a known list, not an enum,
    #: so a newer app can report a reason an older server has not seen.
    stop_reason = db.Column(db.String(30), nullable=True)
    last_update_at = db.Column(db.DateTime, nullable=True)
    device_id = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    vehicle = db.relationship("Vehicle")
    user = db.relationship("User")

    __table_args__ = (
        db.Index("ix_vehicle_tracking_sessions_vehicle_status",
                 "vehicle_id", "status"),
    )


class VehicleLocationLog(db.Model):
    """One reported position. Append-only: never edited, only purged
    after the retention period."""
    __tablename__ = "vehicle_location_logs"

    id = db.Column(db.Integer, primary_key=True)
    #: Generated on the phone; the idempotency key. Re-sending a point
    #: (an offline queue retrying after a dropped response) finds this
    #: row instead of creating a second one.
    client_uuid = db.Column(db.String(36), unique=True, nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    latitude = db.Column(db.Numeric(9, 6), nullable=False)
    longitude = db.Column(db.Numeric(9, 6), nullable=False)
    accuracy_m = db.Column(db.Numeric(8, 2), nullable=False)
    altitude_m = db.Column(db.Numeric(8, 2), nullable=True)
    speed_mps = db.Column(db.Numeric(7, 2), nullable=True)
    heading_deg = db.Column(db.Numeric(5, 2), nullable=True)
    #: Android reports when the position came from a mock-location app.
    #: Stored and shown, never silently dropped: the coordinator decides.
    is_mocked = db.Column(db.Boolean, nullable=False, default=False)
    source = db.Column(db.String(10), nullable=False)  # MANUAL / AUTOMATIC
    captured_at = db.Column(db.DateTime, nullable=False)
    received_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    captured_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                            nullable=False)
    tracking_session_id = db.Column(
        db.Integer, db.ForeignKey("vehicle_tracking_sessions.id"),
        nullable=True)
    device_id = db.Column(db.String(64), nullable=True)

    vehicle = db.relationship("Vehicle")
    reporter = db.relationship("User", foreign_keys=[captured_by])
    session = db.relationship("VehicleTrackingSession")

    __table_args__ = (
        db.Index("ix_vehicle_location_logs_vehicle_captured",
                 "vehicle_id", "captured_at"),
        db.Index("ix_vehicle_location_logs_reporter_captured",
                 "captured_by", "captured_at"),
        db.Index("ix_vehicle_location_logs_session", "tracking_session_id"),
    )


class VehicleLastLocation(db.Model):
    """Pointer to each vehicle's most recently CAPTURED point.

    The map asks "latest point of every visible vehicle" on every
    refresh. With this table that costs one row per vehicle, however
    large the history grows. Moved forward only when a newer point
    arrives -- offline uploads come in out of order.
    """
    __tablename__ = "vehicle_last_locations"

    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           primary_key=True)
    log_id = db.Column(db.Integer, db.ForeignKey("vehicle_location_logs.id"),
                       nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow,
                           onupdate=_utcnow)

    log = db.relationship("VehicleLocationLog")
