"""Driver/Assignee master model."""
from app.extensions import db
from app.core.models.base import BaseModel


class Driver(db.Model, BaseModel):
    __tablename__ = "drivers"

    # Person ID: auto-generated identifier for the Employee/Vehicle
    # Assignee Master module — same auto-numbering pattern as every
    # transaction document, just for a master data record instead.
    person_id = db.Column(db.String(30), unique=True, nullable=True, index=True)
    employee_number = db.Column(db.String(30), unique=True,
                                nullable=False, index=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    suffix = db.Column(db.String(20), nullable=True)
    nickname = db.Column(db.String(80), nullable=True)

    # ASSIGNEE_TYPE lookup: EMPLOYEE | THIRD_PARTY_DELIVERY | CONSULTANT |
    # DRIVER. Only DRIVER-type assignees are required to hold license
    # details on file — an Employee or Consultant can be a vehicle
    # assignee without personally being the one who drives it (e.g. an
    # executive with their own personal driver).
    assignee_type = db.Column(db.String(20), nullable=False, default="DRIVER")

    # License fields are nullable at the DB level (a non-DRIVER assignee
    # genuinely has none), but DriverService.create()/update() enforce
    # them as required whenever assignee_type == "DRIVER" — preserving
    # the exact original requirement for that case.
    license_number = db.Column(db.String(30), unique=True,
                               nullable=True, index=True)
    license_expiry = db.Column(db.Date, nullable=True)
    license_type = db.Column(db.String(20), nullable=True)  # Lookup LICENSE_TYPE

    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"),
                              nullable=True)
    section = db.Column(db.String(100), nullable=True)
    position = db.Column(db.String(100), nullable=True)
    # Needed for the Dynamic PM Work Order Report's "Assignee Position"
    # field (PM7) — kept distinct from `position` above, since the spec
    # lists Position and Job Title as two separate fields.
    job_title = db.Column(db.String(100), nullable=True)
    cost_center = db.Column(db.String(50), nullable=True)
    # EMPLOYMENT_STATUS / EMPLOYMENT_TYPE lookups
    employment_status = db.Column(db.String(20), nullable=True)
    employment_type = db.Column(db.String(20), nullable=True)

    # Contact Information — `phone` (below) serves as Mobile Number;
    # office_number/home_number are the other two numbers from the spec.
    phone = db.Column(db.String(50))
    office_number = db.Column(db.String(50), nullable=True)
    home_number = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(255))
    complete_address = db.Column(db.Text, nullable=True)
    emergency_contact_person = db.Column(db.String(120), nullable=True)
    emergency_contact_number = db.Column(db.String(50), nullable=True)

    # Business Information — for THIRD_PARTY_DELIVERY / CONSULTANT
    # assignee types representing an outside business, not a direct
    # employee.
    business_name = db.Column(db.String(200), nullable=True)
    business_contact_no = db.Column(db.String(50), nullable=True)
    business_address = db.Column(db.Text, nullable=True)

    # ACTIVE | INACTIVE | SUSPENDED
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)

    branch = db.relationship("Branch")
    department = db.relationship("Department")

    @property
    def full_name(self):
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name[0] + ".")
        parts.append(self.last_name)
        if self.suffix:
            parts.append(self.suffix)
        return " ".join(parts)


class EmergencyContact(db.Model, BaseModel):
    """The 'Emergency Contacts & References' grid — a person can have
    multiple emergency contacts/references beyond the single primary
    emergency_contact_person/number fields above, matching the requested
    dual-layout (entry form + dynamic grid)."""
    __tablename__ = "person_emergency_contacts"
    person_record_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                                 nullable=False, index=True)
    contact_name = db.Column(db.String(120), nullable=False)
    relationship_type = db.Column(db.String(50), nullable=True)
    contact_number = db.Column(db.String(50), nullable=True)

    person = db.relationship("Driver", backref="emergency_contacts")
