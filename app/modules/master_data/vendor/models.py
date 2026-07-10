"""Vendor master model."""
from app.extensions import db
from app.core.models.base import BaseModel


class Vendor(db.Model, BaseModel):
    __tablename__ = "vendors"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(255))
    city = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    tin = db.Column(db.String(50))
    contact_person = db.Column(db.String(120))
    # GOODS | SERVICES | BOTH
    vendor_type = db.Column(db.String(10), nullable=False, default="GOODS")
