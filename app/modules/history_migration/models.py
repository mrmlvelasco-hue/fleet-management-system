"""Historical data migration -- bringing paper-record vehicle history
into the system as real Maintenance Order and Vehicle Registration
rows, without disturbing live approval workflows or the live document
numbering sequence.

One HistoricalImportBatch per file uploaded. Every row it creates,
whether a MaintenanceOrder or a VehicleRegistration, is tagged with
that batch's id -- so "the wrong file got uploaded" can be undone as
one action across BOTH transaction types at once, the same guarantee
already built for fuel statement imports.
"""
from app.extensions import db
from app.core.models.base import BaseModel


class HistoricalImportBatch(db.Model, BaseModel):
    __tablename__ = "historical_import_batches"

    filename = db.Column(db.String(255), nullable=True)
    imported_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                           nullable=True)
    imported_at = db.Column(db.DateTime, nullable=True)
    maintenance_rows = db.Column(db.Integer, default=0, nullable=False)
    registration_rows = db.Column(db.Integer, default=0, nullable=False)
    notes = db.Column(db.String(255), nullable=True)

    importer = db.relationship("User")
