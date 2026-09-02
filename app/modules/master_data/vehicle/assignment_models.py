"""Vehicle assignment history.

Before this, `Vehicle.assigned_driver_id` recorded the present and
nothing else -- no dates, no history, and no record of which document
caused the handover, even though all three writers knew. Reassigning a
vehicle overwrote the only evidence that the previous assignment ever
existed.

This table is the record. `Vehicle.assigned_driver_id` REMAINS, and
remains a real column, maintained by VehicleAssignmentService as a cache
of the open row. That is a deliberate choice over deriving it: three
places filter it in SQL (`Vehicle.query.filter_by(assigned_driver_id=…)`
in api/drivers.py and twice in master_data/routes.py), and a Python
property is invisible to SQL. Those queries would not error -- they
would silently return nothing, and the Assignee screen would quietly
stop listing vehicles. A silent wrong answer is worse than a loud break.
"""
from app.core.models.base import BaseModel
from app.extensions import db


class VehicleAssignment(db.Model, BaseModel):
    """One period during which one assignee held one vehicle."""

    __tablename__ = "vehicle_assignments"

    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False, index=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                          nullable=False, index=True)

    #: NULL only on backfilled rows, where it honestly means "in force,
    #: start unknown". Inventing a plausible date at migration time
    #: would put fiction into an audit table, and nobody reading it
    #: later could tell which dates were real.
    assigned_from = db.Column(db.Date, nullable=True)

    #: NULL means OPEN -- this is the current assignment. Every scope
    #: decision in Phase 2 rests on this predicate, so it is the one
    #: piece of this schema that must not acquire a second meaning.
    #:
    #: At most one open row per vehicle. Enforced in the service rather
    #: than by a constraint: the natural expression is a partial unique
    #: index (UNIQUE ... WHERE assigned_to IS NULL), MySQL has no
    #: filtered indexes, and the schema must stay migratable to SQL
    #: Server. A constraint that only existed on one engine would give
    #: false confidence on the others.
    assigned_to = db.Column(db.Date, nullable=True, index=True)

    #: MANUAL | ATD | MO | BACKFILL -- which workflow caused this.
    source = db.Column(db.String(20), nullable=False, default="MANUAL")

    #: The originating document, when there is one. Both hooks already
    #: know this and previously discarded it; keeping it is what lets
    #: the table answer "why did this vehicle change hands on the 14th"
    #: rather than only "who holds it now".
    source_table = db.Column(db.String(50), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)

    remarks = db.Column(db.Text, nullable=True)

    vehicle = db.relationship("Vehicle")
    driver = db.relationship("Driver")

    @property
    def is_open(self) -> bool:
        return self.assigned_to is None
