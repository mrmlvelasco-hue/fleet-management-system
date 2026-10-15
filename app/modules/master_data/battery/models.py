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
    # 2026-10-15: model code (BATTERY_MODEL lookup, e.g. NS40ZL), warranty
    # entered at installation, and the last test reading.
    model = db.Column(db.String(80), nullable=True)
    warranty_months = db.Column(db.Integer, nullable=True)
    mounted_date = db.Column(db.Date, nullable=True)
    mounted_odometer = db.Column(db.Integer, nullable=True)
    last_voltage = db.Column(db.Numeric(5, 2), nullable=True)
    last_test_date = db.Column(db.Date, nullable=True)
    # Which branch's warehouse/stock this battery belongs to — same
    # rationale as Tire.branch_id.
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True)

    vendor = db.relationship("Vendor")
    vehicle = db.relationship("Vehicle")
    branch = db.relationship("Branch")
