"""Battery master model and service."""
from app.extensions import db
from app.core.models.base import BaseModel


class Battery(db.Model, BaseModel):
    __tablename__ = "batteries"
    serial_number = db.Column(db.String(60), unique=True,
                              nullable=False, index=True)
    brand = db.Column(db.String(80), nullable=False)
    capacity_ah = db.Column(db.Integer, nullable=True)
    voltage = db.Column(db.Integer, nullable=True)
    purchase_date = db.Column(db.Date, nullable=True)
    purchase_cost = db.Column(db.Numeric(18, 2), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    # IN_STOCK | MOUNTED | DISPOSED
    status = db.Column(db.String(12), default="IN_STOCK", nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=True)

    vendor = db.relationship("Vendor")
    vehicle = db.relationship("Vehicle")
