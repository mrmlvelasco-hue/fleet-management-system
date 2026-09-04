"""Vehicle Checklist models.

Templates drive the item list so categories are not hardcoded in the UI.
A submitted inspection is an auditable transaction: lines and defects
are snapshotted onto the document and are not rewritten after submit.
"""
from datetime import date, time

from app.extensions import db
from app.core.models.base import BaseModel


class ChecklistTemplate(db.Model, BaseModel):
    __tablename__ = "checklist_templates"

    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    vehicle_type_id = db.Column(db.Integer, db.ForeignKey("vehicle_types.id"),
                                nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")
    categories = db.relationship(
        "ChecklistCategory", backref="template",
        order_by="ChecklistCategory.sort_order",
        cascade="all, delete-orphan")


class ChecklistCategory(db.Model, BaseModel):
    __tablename__ = "checklist_categories"

    template_id = db.Column(db.Integer, db.ForeignKey("checklist_templates.id"),
                            nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    items = db.relationship(
        "ChecklistItem", backref="category",
        order_by="ChecklistItem.sort_order",
        cascade="all, delete-orphan")


class ChecklistItem(db.Model, BaseModel):
    __tablename__ = "checklist_items"

    category_id = db.Column(db.Integer, db.ForeignKey("checklist_categories.id"),
                            nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    required = db.Column(db.Boolean, nullable=False, default=True)
    photo_required_on_fail = db.Column(db.Boolean, nullable=False, default=False)
    maintenance_allowed = db.Column(db.Boolean, nullable=False, default=True)
    is_safety = db.Column(db.Boolean, nullable=False, default=False)
    weight = db.Column(db.Integer, nullable=False, default=1)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(20), nullable=False, default="ACTIVE")


class VehicleChecklist(db.Model, BaseModel):
    __tablename__ = "vehicle_checklists"

    document_number = db.Column(db.String(40), unique=True, nullable=True, index=True)
    template_id = db.Column(db.Integer, db.ForeignKey("checklist_templates.id"),
                            nullable=False, index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False, index=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"), nullable=True)
    # Indexed: every list filters on branch, and at a daily checklist per
    # vehicle this table grows by ~1.8M rows a year for 5,000 vehicles.
    # An unindexed filter there is a full scan on MySQL -- which SQLite
    # at 300k rows will not reveal.
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True, index=True)
    odometer = db.Column(db.Integer, nullable=True)
    # THE most-filtered column once the history becomes a daily log:
    # "what did we find last Tuesday" is the question this table exists
    # to answer.
    inspection_date = db.Column(db.Date, nullable=False, default=date.today,
                                index=True)
    inspection_time = db.Column(db.Time, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="DRAFT",
                       index=True)
    result = db.Column(db.String(20), nullable=True)
    score = db.Column(db.Numeric(5, 2), nullable=True)
    applicable_count = db.Column(db.Integer, nullable=True)
    pass_count = db.Column(db.Integer, nullable=True)
    attention_count = db.Column(db.Integer, nullable=True)
    fail_count = db.Column(db.Integer, nullable=True)
    submitted_at = db.Column(db.DateTime, nullable=True)
    submitted_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    remarks = db.Column(db.Text, nullable=True)

    template = db.relationship("ChecklistTemplate")
    vehicle = db.relationship("Vehicle")
    driver = db.relationship("Driver")
    branch = db.relationship("Branch")
    lines = db.relationship(
        "VehicleChecklistLine", backref="checklist",
        cascade="all, delete-orphan")
    defects = db.relationship(
        "ChecklistDefect", backref="checklist",
        cascade="all, delete-orphan")


class VehicleChecklistLine(db.Model, BaseModel):
    __tablename__ = "vehicle_checklist_lines"

    checklist_id = db.Column(db.Integer, db.ForeignKey("vehicle_checklists.id"),
                             nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("checklist_items.id"),
                        nullable=False)
    category_name = db.Column(db.String(120), nullable=False)
    item_name = db.Column(db.String(150), nullable=False)
    response = db.Column(db.String(20), nullable=True)
    is_required = db.Column(db.Boolean, nullable=False, default=True)
    is_safety = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    #: Evidence for an ATTENTION or FAILED response.
    #:
    #: On the LINE, not the defect. A response is what the driver
    #: records; a defect is the observation that follows it. Hanging the
    #: photo off the defect would mean the picture only exists once
    #: someone has written a description, which is backwards on a phone
    #: -- the driver photographs the cracked light and then types.
    #:
    #: An attachment id, not bytes: this table has a row per checklist
    #: ITEM (45 on the BLOWBAGETS template), so a blob column here would
    #: be loaded on every line of every inspection.
    #:
    #: items.photo_required_on_fail already existed and the BLOWBAGETS
    #: seed sets it on all four safety categories -- the intent was
    #: modelled long before there was anywhere to put the picture.
    photo_attachment_id = db.Column(db.Integer, nullable=True)

    item = db.relationship("ChecklistItem")


class ChecklistDefect(db.Model, BaseModel):
    __tablename__ = "checklist_defects"

    checklist_id = db.Column(db.Integer, db.ForeignKey("vehicle_checklists.id"),
                             nullable=False, index=True)
    line_id = db.Column(db.Integer, db.ForeignKey("vehicle_checklist_lines.id"),
                        nullable=True)
    item_name = db.Column(db.String(150), nullable=False)
    observation = db.Column(db.Text, nullable=True)
    severity = db.Column(db.String(20), nullable=False, default="MINOR")
    create_maintenance = db.Column(db.Boolean, nullable=False, default=False)
    maintenance_order_id = db.Column(
        db.Integer, db.ForeignKey("maintenance_orders.id"), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="OPEN")

    line = db.relationship("VehicleChecklistLine")
    maintenance_order = db.relationship("MaintenanceOrder")
