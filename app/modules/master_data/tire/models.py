"""Tire master model and service."""
from app.extensions import db
from app.core.models.base import BaseModel


class Tire(db.Model, BaseModel):
    __tablename__ = "tires"
    serial_number = db.Column(db.String(60), unique=True,
                              nullable=False, index=True)
    brand = db.Column(db.String(80), nullable=False)
    size = db.Column(db.String(30), nullable=False)
    tire_type = db.Column(db.String(10), nullable=False)  # RADIAL | BIAS
    purchase_date = db.Column(db.Date, nullable=True)
    purchase_cost = db.Column(db.Numeric(18, 2), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    tread_depth_initial = db.Column(db.Numeric(5, 2), nullable=True)
    # IN_STOCK | MOUNTED | RETREADED | DISPOSED
    status = db.Column(db.String(12), default="IN_STOCK", nullable=False)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=True)  # set when mounted
    wheel_position = db.Column(db.String(20), nullable=True)

    vendor = db.relationship("Vendor")
    vehicle = db.relationship("Vehicle")
