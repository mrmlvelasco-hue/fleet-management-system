"""Vendor master model.

Vendors are deliberately NOT restricted to a single branch like
Vehicle/Driver/Tire/Battery — a vendor (e.g. a repair shop) can
legitimately serve several branches or business units that are close
enough to be practical (e.g. a Laguna shop also covering Manila, ~1.5hrs
away), so this is a many-to-many relationship rather than a single
branch_id column.
"""
from app.extensions import db
from app.core.models.base import BaseModel

vendor_branches = db.Table(
    "vendor_branches",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("branch_id", db.Integer, db.ForeignKey("branches.id"),
              primary_key=True),
)

vendor_business_units = db.Table(
    "vendor_business_units",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("business_unit_id", db.Integer,
              db.ForeignKey("business_units.id"), primary_key=True),
)


class VendorContact(db.Model, BaseModel):
    """A supplier can have multiple secondary/alternate contacts (e.g. an
    Account Manager, a Sales Rep) beyond the single contact_person field
    on Vendor itself — a genuine One-to-Many relationship."""
    __tablename__ = "vendor_contacts"
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=False, index=True)
    contact_name = db.Column(db.String(120), nullable=False)
    tel_number = db.Column(db.String(50), nullable=True)
    cel_number = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    position = db.Column(db.String(100), nullable=True)

    vendor = db.relationship("Vendor", backref="other_contacts")


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

    # No branches/BUs assigned at all = serves everyone (e.g. a nationwide
    # supplier) — same "no org context = unrestricted" convention used
    # elsewhere. Assign one or more to scope it to specific branches/BUs.
    branches = db.relationship("Branch", secondary=vendor_branches,
                               backref="vendors")
    business_units = db.relationship("BusinessUnit",
                                     secondary=vendor_business_units,
                                     backref="vendors")
