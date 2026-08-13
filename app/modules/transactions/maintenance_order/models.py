"""Maintenance Order transaction model (Preventive + Corrective) with an
optional generated checklist copied from a PM Scope Template.
"""
from app.extensions import db
from app.core.models.base import BaseModel


# Shared with DashboardAnalyticsService.maintenance_orders_by_type() --
# ONE place both the dashboard chart's SQL classification and the MO
# list's per-row column read from, so a label can never drift between
# the two. Previously the list showed only the raw maintenance_type or
# transaction_type name (or a bare dash for an OPERATIONAL order, which
# has neither), with no way to see which of the dashboard's four
# reporting buckets a given order actually falls into.
MAINTENANCE_CLASS_LABELS = {
    "PREVENTIVE": "Preventive Maintenance",
    "CORRECTIVE": "Corrective Maintenance",
    "PREDICTIVE": "Predictive Maintenance",
    "OPERATIONAL": "Operational",
    "UNCLASSIFIED": "Unclassified",
}


class TransactionType(db.Model, BaseModel):
    """Per the MO Module Enhancement spec: 'Every Transaction Type
    belongs to exactly one Category.' Admin-configurable master data —
    not hardcoded, so new operational transaction types can be added
    without a code change. `group` is purely for organizing the New MO
    form's dropdown into optgroups (Deployment/Administrative/Disposal/
    Accessories/Maintenance) — it isn't a third hierarchy level, just a
    display aid, since a flat list of 40+ types in one dropdown would be
    unusable."""
    __tablename__ = "mo_transaction_types"

    code = db.Column(db.String(30), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    # MAINTENANCE | OPERATIONAL
    order_category = db.Column(db.String(15), nullable=False)
    # DEPLOYMENT | ADMINISTRATIVE | DISPOSAL | ACCESSORIES | MAINTENANCE
    group = db.Column(db.String(20), nullable=True)
    # PREVENTIVE | CORRECTIVE | PREDICTIVE — the maintenance discipline a
    # MAINTENANCE-category transaction represents, used by the dashboard's
    # "Maintenance Orders by Type" analytics.
    #
    # Held as DATA on the transaction type rather than as a hardcoded
    # CASE in the query. The reporting requirement arrived as a 40-code
    # SQL CASE, but 25 of those codes are simply "every OPERATIONAL type"
    # -- already answered by order_category -- and encoding the rest in a
    # query means adding a new maintenance type later silently lands in
    # "Unclassified" until someone edits SQL. As a column it is visible
    # and editable in MO Transaction Types maintenance, consistent with
    # this system's rule that business classification is configuration,
    # not code.
    #
    # NULL is meaningful: an OPERATIONAL type has no maintenance class.
    maintenance_class = db.Column(db.String(20), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)


class MaintenanceOrder(db.Model, BaseModel):
    __tablename__ = "maintenance_orders"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=False)
    # Operational-category orders (Deployment, Administrative, Disposal,
    # Accessories) are valid work requests that aren't vehicle
    # maintenance at all — so unlike before, this is now nullable.
    maintenance_type_id = db.Column(db.Integer,
                                    db.ForeignKey("maintenance_types.id"),
                                    nullable=True)
    # PREVENTIVE | CORRECTIVE — copied from MaintenanceType at creation
    # time. Nullable for the same reason as maintenance_type_id above.
    category = db.Column(db.String(12), nullable=True)
    # MAINTENANCE | OPERATIONAL — the top-level split from the MO Module
    # Enhancement spec. Defaults to MAINTENANCE so every existing order
    # (and every order created the same way as before) is unaffected.
    order_category = db.Column(db.String(15), nullable=False,
                               default="MAINTENANCE")
    transaction_type_id = db.Column(db.Integer,
                                    db.ForeignKey("mo_transaction_types.id"),
                                    nullable=True)
    pm_schedule_id = db.Column(db.Integer, db.ForeignKey("pm_schedules.id"),
                               nullable=True)
    scope_template_id = db.Column(db.Integer,
                                  db.ForeignKey("pm_scope_templates.id"),
                                  nullable=True)
    description = db.Column(db.Text, nullable=True)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    scheduled_date = db.Column(db.Date, nullable=False)
    completed_date = db.Column(db.Date, nullable=True)
    assigned_mechanic = db.Column(db.String(120), nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    # Used by Operational/Deployment orders (Assignment, Reassignment,
    # Relocation, Transfer) as the "Vehicle Assignment Memo" — the formal
    # paper trail for a driver assignment change. On completion, this
    # updates Vehicle.assigned_driver_id the same way an approved ATD
    # does (see assignment_hooks.py) -- either mechanism can be the
    # operative record depending on which document your process uses.
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                          nullable=True)
    # Used by Operational/Deployment orders with Transaction Type
    # Relocation/Transfer -- the vehicle's destination branch. On
    # completion, this both updates Vehicle.branch_id (the vehicle is
    # now physically/organizationally at the new branch) and generates
    # transfer_reference_number, printed as the "ATR No." on the Asset
    # Transfer Report (see driver_print-style print route in routes.py).
    destination_branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                                      nullable=True)
    # Snapshot of the vehicle's branch AT THE TIME this order was
    # created -- needed because completion updates Vehicle.branch_id to
    # the destination, so by the time the Asset Transfer Report is
    # printed, the vehicle's CURRENT branch is already the new one, not
    # the "From" branch the report needs to show.
    origin_branch_id = db.Column(db.Integer, db.ForeignKey("branches.id"),
                                 nullable=True)
    # Generated once, at completion, via AutoNumberingService.generate("ATR")
    # -- a separate series from the MO's own document_number (MO-2026-...)
    # since the Asset Transfer Report is treated as its own printable
    # document, following the pattern of ATD/PR/TT each having their own
    # numbering series.
    transfer_reference_number = db.Column(db.String(40), nullable=True)
    # Retirement/Disposal -- completes the Acquisition-to-Retirement asset
    # lifecycle. The disposal REASON/METHOD is the selected Transaction
    # Type itself (Scrappage/Carnapped/Total Loss/Uneconomical/Sold/
    # Donated -- the DISPOSAL transaction-type group), not a separate
    # field, so there's exactly one place to record it. These two fields
    # capture what the transaction type alone can't:
    disposal_value = db.Column(db.Numeric(18, 2), nullable=True)
    # Optional (per client decision) -- proceeds/value for asset
    # accounting, meaningful mainly for Sold/Auctioned/Donated/Total
    # Loss (insurance settlement), less so for Scrappage/Carnapped.
    disposal_recipient = db.Column(db.String(150), nullable=True)
    # Buyer, auction house, donation recipient, or insurance company --
    # optional context, not required for any disposal method.
    disposal_reference_number = db.Column(db.String(40), nullable=True)
    # Generated once, at completion, via AutoNumberingService.generate("ADR")
    # -- printed as the "ADR No." on the Asset Disposal Report, the
    # retirement-stage counterpart to the Asset Transfer Report's ATR No.
    # PERK | TOOL_OF_THE_TRADE | OPERATIONS_SERVICE_UNIT | EDS_UNIT --
    # which of the four entitlement categories on the Vehicle Assignment
    # Memo applies to this handover. Only meaningful for
    # Assignment/Reassignment transaction types, alongside driver_id.
    assignment_classification = db.Column(db.String(30), nullable=True)
    estimated_cost = db.Column(db.Numeric(18, 2), nullable=True)
    actual_cost = db.Column(db.Numeric(18, 2), nullable=True)

    # DRAFT | PENDING | APPROVED | IN_PROGRESS | COMPLETED | CANCELLED —
    # physical lifecycle, separate from the linked ApprovalInstance.status.
    status = db.Column(db.String(12), default="DRAFT", nullable=False)
    # A row imported from paper records, not created through the live
    # workflow. Historical rows import directly as COMPLETED with no
    # approval_instance -- they must never appear in "For My Action" or
    # count toward this period's approval statistics; the work already
    # happened, sometimes years ago.
    is_historical = db.Column(db.Boolean, default=False, nullable=False)
    historical_batch_id = db.Column(
        db.Integer, db.ForeignKey("historical_import_batches.id"),
        nullable=True)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    # The draft Purchase Request auto-generated from this order's parts
    # list. Nullable: an order needing no procured parts never generates
    # one, and orders created before this feature have none.
    purchase_request_id = db.Column(
        db.Integer, db.ForeignKey("purchase_requests.id"), nullable=True)
    purchase_request = db.relationship("PurchaseRequest")

    vehicle = db.relationship("Vehicle")

    @property
    def cost_center(self):
        """The cost centre this order's expenses belong to, resolved
        through its own vehicle's department assignment.

        No cost-centre column on the Maintenance Order itself: the
        vehicle already carries department_id, so the chain
        (Order -> Vehicle -> Department -> cost_center) answers this
        without duplicating the value or asking the user to re-enter
        something the system already knows. Works identically for PMS
        and Operational orders, since both are always against a vehicle.
        """
        return self.vehicle.cost_center if self.vehicle else None
    maintenance_type = db.relationship("MaintenanceType")
    transaction_type = db.relationship("TransactionType")
    pm_schedule = db.relationship("PMSchedule")
    scope_template = db.relationship("PMScopeTemplate")
    vendor = db.relationship("Vendor")
    driver = db.relationship("Driver")
    destination_branch = db.relationship("Branch",
                                         foreign_keys=[destination_branch_id])
    origin_branch = db.relationship("Branch", foreign_keys=[origin_branch_id])
    requester = db.relationship("User", foreign_keys=[requested_by])

    @property
    def maintenance_class_bucket(self):
        """PREVENTIVE / CORRECTIVE / PREDICTIVE / OPERATIONAL /
        UNCLASSIFIED -- the exact same four (or five) buckets the
        Dashboard's "Maintenance Orders by Type" chart counts into.

        Mirrors DashboardAnalyticsService.maintenance_orders_by_type()'s
        SQL case() branch for branch, deliberately, rather than being a
        second, independently-written rule that could quietly drift
        from it -- a test asserts they agree on the same real data.
        """
        if self.order_category == "OPERATIONAL":
            return "OPERATIONAL"
        if self.transaction_type is not None:
            if self.transaction_type.maintenance_class:
                return self.transaction_type.maintenance_class
        elif self.transaction_type_id is None:
            # No transaction type recorded (older PM orders that predate
            # transaction types) is preventive by definition.
            return "PREVENTIVE"
        return "UNCLASSIFIED"

    @property
    def maintenance_class_label(self):
        return MAINTENANCE_CLASS_LABELS.get(
            self.maintenance_class_bucket, "Unclassified")
    approval_instance = db.relationship("ApprovalInstance")
    checklist_items = db.relationship(
        "MaintenanceChecklistItem", backref="order",
        order_by="MaintenanceChecklistItem.sort_order",
        cascade="all, delete-orphan")


class MaintenanceChecklistItem(db.Model, BaseModel):
    __tablename__ = "maintenance_checklist_items"
    order_id = db.Column(db.Integer, db.ForeignKey("maintenance_orders.id"),
                         nullable=False)
    activity_code = db.Column(db.String(40), nullable=False)
    activity_description = db.Column(db.String(255), nullable=False)
    is_done = db.Column(db.Boolean, default=False, nullable=False)
    done_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    done_at = db.Column(db.DateTime, nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    done_by_user = db.relationship("User")


class MaintenanceOrderPart(db.Model, BaseModel):
    """A part or material the mechanic has determined must be procured
    for this order.

    This is deliberately NOT an inventory record -- there is no stock
    level, no warehouse, no reservation. It is a statement of what needs
    buying, captured once at the point the mechanic actually knows it,
    and carried straight through to the Purchase Request so the same
    items never get typed a second time.

    estimated_unit_cost is an estimate by design: the mechanic is
    stating a requirement, not a settled price. The real price arrives
    later on the supplier's invoice.
    """
    __tablename__ = "maintenance_order_parts"

    order_id = db.Column(db.Integer, db.ForeignKey("maintenance_orders.id"),
                        nullable=False, index=True)
    part_number = db.Column(db.String(60), nullable=True)
    part_description = db.Column(db.String(255), nullable=False)
    specification = db.Column(db.String(255), nullable=True)
    uom = db.Column(db.String(20), nullable=True)
    quantity = db.Column(db.Numeric(12, 2), nullable=False, default=1)
    estimated_unit_cost = db.Column(db.Numeric(18, 2), nullable=False,
                                   default=0)
    remarks = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    order = db.relationship("MaintenanceOrder", backref=db.backref(
        "parts", cascade="all, delete-orphan", lazy="selectin"))

    @property
    def estimated_total(self):
        return (self.quantity or 0) * (self.estimated_unit_cost or 0)
