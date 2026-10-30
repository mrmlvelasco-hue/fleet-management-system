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

# Stage 3D: the Company counterpart of vendor_branches. Vendors serve
# MANY branches, so the Company side must also be an association -- a
# vendor serving three Companies could not be recorded on a single
# column. Additive: vendor_branches is untouched.
vendor_companies = db.Table(
    "vendor_companies",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("company_id", db.Integer, db.ForeignKey("org_companies.id"),
              primary_key=True),
)

vendor_business_units = db.Table(
    "vendor_business_units",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("business_unit_id", db.Integer,
              db.ForeignKey("business_units.id"), primary_key=True),
)

# Batch 7: the Region/Area-District counterparts of vendor_companies,
# for a vendor assigned to a whole Region or Area-District rather than
# one Company at a time -- "this vendor covers all of Luzon" needs its
# own association, the same reason vendor_companies couldn't just be a
# single column on Vendor.
vendor_regions = db.Table(
    "vendor_regions",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("region_id", db.Integer, db.ForeignKey("regions.id"),
              primary_key=True),
)

vendor_area_districts = db.Table(
    "vendor_area_districts",
    db.Column("vendor_id", db.Integer, db.ForeignKey("vendors.id"),
              primary_key=True),
    db.Column("area_district_id", db.Integer,
              db.ForeignKey("areas_districts.id"), primary_key=True),
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

    # No branches/BUs/companies/regions/area-districts assigned at all =
    # serves everyone (e.g. a nationwide supplier) — same "no org
    # context = unrestricted" convention used elsewhere. Assign one or
    # more of ANY of these five to scope it; VendorService._is_visible()
    # treats them as alternatives (any one matching is enough), not a
    # combined intersection.
    branches = db.relationship("Branch", secondary=vendor_branches,
                               backref="vendors")
    business_units = db.relationship("BusinessUnit",
                                     secondary=vendor_business_units,
                                     backref="vendors")
    # Batch 7 (2026-10-31): Company/Region/Area-District scoping,
    # alongside Branch/BU rather than replacing them -- a vendor that
    # already serves "Manila Hub" can keep that AND also be given
    # "all of Excellence Poultry" without re-entering anything.
    companies = db.relationship("OrgCompany", secondary=vendor_companies,
                                backref="vendors")
    regions = db.relationship("Region", secondary=vendor_regions,
                              backref="vendors")
    area_districts = db.relationship("AreaDistrict",
                                     secondary=vendor_area_districts,
                                     backref="vendors")
