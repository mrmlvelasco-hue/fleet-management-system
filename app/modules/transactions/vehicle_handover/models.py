"""Vehicle Handover Checklist -- issuance/return, standalone.

Deliberately independent of the periodic-inspection checklist module
(app/modules/transactions/vehicle_checklist) and of the mobile field
APK. Michael: "This separate to the checklist module and APK because if
my clients get this system excluded the checklist and APK this will be
the alternative ways for checklist." If checklist_templates,
checklist_categories, checklist_items and checklist_documents were
dropped entirely, this module must keep working unchanged -- that is
precisely the scenario it exists to cover.

That is why the accessory list below is this module's OWN small,
hardcoded default rather than a ChecklistTemplate lookup: the paper
form's item list IS the specification here, not a configurable
catalogue borrowed from a module this one must survive without.

Reused infrastructure is limited to things that are not themselves the
checklist module: BaseModel's audit columns, the generic Auto Numbering
Engine (VHC-2026-000001, same engine Vehicle Registration and Purchase
Request use), and the generic Attachment service for damage photos.
"""
from app.core.models.base import BaseModel
from app.extensions import db

#: The paper form's Section III list, in print order. A module-level
#: constant, not a database table -- see the module docstring for why.
DEFAULT_ITEMS = [
    "Keys", "Lighter", "Ashtray", "Seat Cover", "Floor Mats", "Seat Belt",
    "Head Rest", "Sun Visor Shade", "Horn", "Wiper",
    "Left Side View Mirror", "Right Side View Mirror", "Rear View Mirror",
    "Radiator Cap", "Gas Cap", "Oil Cap", "Dip Stick",
    "Plate License (F/R)", "Mudguard", "Windshield", "Back Glass",
    "Door Glass",
    "Center Hub Caps", "Spare Tire", "Spare Tire Cover", "Tire Wrench",
    "Tires (Size)", "Tire Rims / Mags", "Jack with Handle", "Tool Bag",
    "Screw Driver / Philips", "Mach. Pliers", "Spark Plug Wrench w/ Handle",
    "Open Wrench", "Antenna", "Car Stereo with built-in CD Player",
    "Speakers", "Aircon Compressor", "Alarm",
    "Emblem / Nameplate", "Grille / Garnish", "Early Warning Device (EWO)",
    "Operator's Manual",
]

MARK_VALUES = ("OK", "MISSING", "DEFECTIVE")
DAMAGE_KINDS = ("SCRATCH", "DENT", "CHIP")
STATUSES = ("DRAFT", "ISSUED", "RETURNED", "CANCELLED")


class VehicleHandoverChecklist(db.Model, BaseModel):
    """One issuance/return cycle for one vehicle."""

    __tablename__ = "vehicle_handover_checklists"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False, index=True)
    branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                          nullable=True, index=True)

    #: Linked when the assignee has a Driver record; nullable because
    #: the paper form's "Name of Assignee" is filled for anyone handed a
    #: vehicle, including someone not yet in Driver master.
    assignee_driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                                   nullable=True)
    #: SNAPSHOT, not a join. Copied once at creation so a later edit to
    #: the Driver record cannot rewrite a document someone already
    #: signed -- confirmed by test_assignee_is_snapshotted_not_just_linked.
    assignee_name = db.Column(db.String(160), nullable=True)
    position = db.Column(db.String(120), nullable=True)
    company_bu = db.Column(db.String(120), nullable=True)

    status = db.Column(db.String(12), nullable=False, default="DRAFT")

    date_issued = db.Column(db.Date, nullable=True)
    odometer_at_issuance = db.Column(db.Integer, nullable=True)
    issued_by_name = db.Column(db.String(160), nullable=True)
    issued_by_date = db.Column(db.Date, nullable=True)
    issuance_received_by_name = db.Column(db.String(160), nullable=True)
    issuance_received_by_date = db.Column(db.Date, nullable=True)

    date_returned = db.Column(db.Date, nullable=True)
    odometer_at_return = db.Column(db.Integer, nullable=True)
    returned_by_name = db.Column(db.String(160), nullable=True)
    returned_by_date = db.Column(db.Date, nullable=True)
    return_received_by_name = db.Column(db.String(160), nullable=True)
    return_received_by_date = db.Column(db.Date, nullable=True)

    #: 0 (empty) to 4 (full) -- the paper form's single gauge, read at
    #: issuance. See the handover doc for why this is not tracked twice.
    fuel_level = db.Column(db.Integer, nullable=True)

    #: Short, fixed-shape checklists (Section II Documents, Section IV
    #: Other Items). JSON rather than child tables: unlike the accessory
    #: list, these have no per-row structure worth a table, and the
    #: project already uses JSON generically for this shape (AuditLog's
    #: old_values/new_values) with SQLite/MySQL/MSSQL portability in
    #: mind. [{"label": str, "present": bool}, ...]
    documents_json = db.Column(db.JSON, nullable=True)
    other_items_json = db.Column(db.JSON, nullable=True)

    remarks = db.Column(db.Text, nullable=True)

    vehicle = db.relationship("Vehicle")
    branch = db.relationship("Branch")
    assignee_driver = db.relationship("Driver")
    items = db.relationship(
        "HandoverItem", backref="handover",
        order_by="HandoverItem.sort_order",
        cascade="all, delete-orphan")
    damages = db.relationship(
        "HandoverDamageMark", backref="handover",
        cascade="all, delete-orphan")


class HandoverItem(db.Model, BaseModel):
    """One accessory row, carrying BOTH handover moments.

    Two marks per row, not one, because the whole point of this document
    is the comparison between them -- a periodic-inspection item has
    exactly one response, which is the reason this is not built on the
    other checklist module's item model.
    """

    __tablename__ = "vehicle_handover_items"

    handover_id = db.Column(
        db.Integer, db.ForeignKey("vehicle_handover_checklists.id"),
        nullable=False, index=True)
    item_name = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    #: Quantity lives in ITS OWN column, separate from the mark. The
    #: paper form writes a bare "2" beside Ashtray in the Return column
    #: -- that is a count, not a condition, and sharing one field would
    #: make "2" and "OK" the same kind of value.
    issuance_mark = db.Column(db.String(10), nullable=True)  # MARK_VALUES
    issuance_qty = db.Column(db.Integer, nullable=True)
    return_mark = db.Column(db.String(10), nullable=True)
    return_qty = db.Column(db.Integer, nullable=True)


class HandoverDamageMark(db.Model, BaseModel):
    """One scratch/dent/chip placed on the body diagram.

    x/y are PERCENTAGES of the diagram box (0-100), not pixels, so a
    mark holds its place on the body at any print or screen scale --
    the same convention react-v191's DamageMark already uses, kept
    identical here so the API needs no translation layer between them.
    """

    __tablename__ = "vehicle_handover_damage_marks"

    handover_id = db.Column(
        db.Integer, db.ForeignKey("vehicle_handover_checklists.id"),
        nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # DAMAGE_KINDS
    x = db.Column(db.Float, nullable=False)
    y = db.Column(db.Float, nullable=False)
    note = db.Column(db.String(255), nullable=True)
