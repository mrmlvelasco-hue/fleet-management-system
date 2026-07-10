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


class Department(db.Model, BaseModel):
    __tablename__ = "departments"
    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=False)
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
