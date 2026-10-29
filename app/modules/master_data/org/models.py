"""Organisational master data: Branch, Department, BusinessUnit."""
from app.extensions import db
from app.core.models.base import BaseModel


class Branch(db.Model, BaseModel):
    __tablename__ = "branches"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    address = db.Column(db.String(255))
    city = db.Column(db.String(100))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    manager_user_id = db.Column(db.Integer, db.ForeignKey("users.id"),
                                nullable=True)
    manager = db.relationship("User", foreign_keys=[manager_user_id])

    # Stage 3B follow-up: ONE Company per Branch (the EDL Group
    # hierarchy -- a Company such as "Excellence" has several Branches
    # under it, e.g. San Mateo, Cavite 2, South Luzon). Stored here,
    # once, rather than duplicated as its own company_id column on
    # every branch-scoped master (Driver, Department, ...): anything
    # that already has branch_id gets Company for free via
    # `branch.company_id`, with no schema change and no separate
    # migration of its own.
    #
    # Nullable because this predates the real Branch->Company mapping
    # sheet -- every existing Branch is simply unassigned until the
    # migration tool (company_migration.py) sets it.
    #
    # Vehicle keeps its OWN company_id (Stage 3A/3B) rather than always
    # deriving it from branch_id -- that column is the deliberate,
    # per-vehicle override Stage 3A already allows (a vehicle manually
    # reassigned to a different Company than its Branch's default).
    # This column is the DEFAULT every branch-scoped record without its
    # own override inherits; it is not meant to replace that override.
    company_id = db.Column(db.Integer, db.ForeignKey("org_companies.id"),
                           nullable=True)
    company = db.relationship("OrgCompany", foreign_keys=[company_id])


class Department(db.Model, BaseModel):
    __tablename__ = "departments"
    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=False)
    # Stage 3D: organisational owner, carried here so it survives
    # Branch's retirement. NOTE the uniqueness constraint below is still
    # (code, branch_id) -- retiring Branch means re-keying it to
    # (code, company_id), which can collide in existing data and is
    # therefore a deliberate later step, not part of this additive one.
    company_id = db.Column(db.Integer,
                           db.ForeignKey("org_companies.id"),
                           nullable=True, index=True)
    # The financial allocation code for this department. Nullable: not
    # every organisation tracks cost centres, and every department that
    # already exists predates this field -- making it required would
    # make those records unsaveable on the next unrelated edit.
    cost_center = db.Column(db.String(40), nullable=True, index=True)
    description = db.Column(db.String(255))
    branch = db.relationship("Branch", backref="departments")

    __table_args__ = (
        db.UniqueConstraint("code", "branch_id", name="uq_dept_code_branch"),
    )


class BusinessUnit(db.Model, BaseModel):
    __tablename__ = "business_units"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))


class Region(db.Model, BaseModel):
    """Configurable master (Stage 1 §5/D3): a Company's Region is
    always one of these, never free text -- the client's own workbook
    misspelling ("EDL PEtHealth") is exactly the failure mode a
    dedicated master table exists to prevent."""
    __tablename__ = "regions"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))


class AreaDistrict(db.Model, BaseModel):
    """Belongs to exactly one Region. The same code may be reused in a
    different Region (e.g. two branches each naming an area "SNM") --
    the uniqueness key is (code, region), not code alone."""
    __tablename__ = "areas_districts"
    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"),
                          nullable=False)
    description = db.Column(db.String(255))
    region = db.relationship("Region", backref="areas_districts")

    __table_args__ = (
        db.UniqueConstraint("code", "region_id",
                            name="uq_area_district_code_region"),
    )


class OrgCompany(db.Model, BaseModel):
    """Company master data (Stage 1 §5/D2). Deliberately allows the
    SAME short_name/company_name to repeat under a DIFFERENT Region +
    Area/District -- Michael's own example is "Excellence" in
    Luzon/San Mateo and "Excellence" in Visayas/Bacolod, two distinct
    records -- so uniqueness is on (short_name, region, area), never
    on short_name alone.

    `legacy_branch_id` is populated only by the Stage 3B migration
    tool, when a Branch maps to exactly one company; it is never set
    by this CRUD and never required."""
    __tablename__ = "org_companies"
    short_name = db.Column(db.String(80), nullable=False, index=True)
    company_name = db.Column(db.String(200), nullable=True)
    region_id = db.Column(db.Integer, db.ForeignKey("regions.id"),
                          nullable=False)
    area_district_id = db.Column(db.Integer,
                                 db.ForeignKey("areas_districts.id"),
                                 nullable=False)
    legacy_branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                                 nullable=True)
    region = db.relationship("Region")
    area_district = db.relationship("AreaDistrict")
    legacy_branch = db.relationship("Branch", foreign_keys=[legacy_branch_id])

    __table_args__ = (
        db.UniqueConstraint("short_name", "region_id", "area_district_id",
                            name="uq_company_shortname_region_area"),
    )
