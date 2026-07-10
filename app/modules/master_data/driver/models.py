"""Driver/Assignee master model."""
from app.extensions import db
from app.core.models.base import BaseModel


class Driver(db.Model, BaseModel):
    __tablename__ = "drivers"
    employee_number = db.Column(db.String(30), unique=True,
                                nullable=False, index=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    middle_name = db.Column(db.String(80))
    license_number = db.Column(db.String(30), unique=True,
                               nullable=False, index=True)
    license_expiry = db.Column(db.Date, nullable=False)
    license_type = db.Column(db.String(20), nullable=False)  # Lookup LICENSE_TYPE
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey("departments.id"),
                              nullable=True)
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
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
        return " ".join(parts)
