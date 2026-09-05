"""Odometer reading history.

Every reading pushed from the field app -- or posted by a telematics
integration, or typed on the web -- overwrote Vehicle.current_odometer
and left no trace. The client asked how to check what a driver had
submitted; the honest answer was that nothing recorded it.

That matters beyond curiosity. current_odometer drives PM due status, so
a wrong reading silently changes when a vehicle is called in for
service, and without a log there is no way to see what it was before or
who changed it.
"""
from app.core.models.base import BaseModel
from app.extensions import db


class VehicleOdometerLog(db.Model, BaseModel):
    """One recorded reading."""

    __tablename__ = "vehicle_odometer_logs"

    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False, index=True)

    #: The value submitted.
    reading = db.Column(db.Integer, nullable=False)

    #: What the vehicle read immediately BEFORE this entry.
    #:
    #: Stored rather than derived from the previous row: it makes "who
    #: moved it by 40,000 km" answerable with one row instead of a
    #: self-join, and it stays correct even if an earlier entry is ever
    #: corrected.
    previous_reading = db.Column(db.Integer, nullable=True)

    #: MOBILE | WEB | API | CHECKLIST | ATD | FUEL
    #:
    #: The client's actual question was "how do I check the update
    #: pushed from mobile", which cannot be answered unless the source
    #: is recorded at the moment of writing.
    source = db.Column(db.String(20), nullable=False, default="WEB",
                       index=True)

    #: The document that carried the reading, when there was one.
    source_table = db.Column(db.String(50), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)

    recorded_at = db.Column(db.DateTime, nullable=False, index=True)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                            nullable=True, index=True)

    remarks = db.Column(db.Text, nullable=True)

    vehicle = db.relationship("Vehicle")
    user = db.relationship("User", foreign_keys=[recorded_by])

    @property
    def delta(self):
        """Distance implied by this reading. None when there is no
        previous value to compare against -- the first reading for a
        vehicle implies nothing."""
        if self.previous_reading is None:
            return None
        return self.reading - self.previous_reading
