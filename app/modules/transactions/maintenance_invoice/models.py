"""Maintenance Invoice & Actual Expense Management.

Per the enhancement doc's own recommendation: a separate transaction
linked to the Maintenance Order (not a tab on it), since one MO can have
multiple invoices (e.g. parts from one supplier, labor from another).
"""
from app.extensions import db
from app.core.models.base import BaseModel


class MaintenanceInvoice(db.Model, BaseModel):
    __tablename__ = "maintenance_invoices"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    maintenance_order_id = db.Column(db.Integer,
                                     db.ForeignKey("maintenance_orders.id"),
                                     nullable=False, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=False, index=True)

    # ── Invoice Header ──────────────────────────────────────────────
    invoice_number = db.Column(db.String(60), nullable=False)
    invoice_date = db.Column(db.Date, nullable=False)
    or_number = db.Column(db.String(60), nullable=True)
    po_number = db.Column(db.String(60), nullable=True)
    dr_number = db.Column(db.String(60), nullable=True)
    # VAT_EXCLUSIVE | VAT_INCLUSIVE | NON_VAT
    vat_type = db.Column(db.String(15), default="VAT_EXCLUSIVE", nullable=False)
    vat_percentage = db.Column(db.Numeric(5, 2), default=12, nullable=False)
    currency = db.Column(db.String(3), default="PHP", nullable=False)
    # Exchange Rate: explicitly called out in the spec as a FUTURE
    # enhancement, not built now — currency is captured today so that
    # groundwork isn't blocked later.

    # DRAFT | SUBMITTED | VERIFIED | APPROVED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)
    requested_by = db.Column(db.Integer, nullable=True)
    approval_instance_id = db.Column(db.Integer,
                                     db.ForeignKey("approval_instances.id"),
                                     nullable=True)
    approval_instance = db.relationship("ApprovalInstance")

    @property
    def vehicle(self):
        """Proxies through the linked Maintenance Order so
        BaseTransactionService._infer_branch_id()'s "vehicle.branch_id"
        path-walk resolves org-scope correctly without needing a direct
        FK duplicate of what the Maintenance Order already has."""
        return self.maintenance_order.vehicle if self.maintenance_order else None

    # ── Auto-calculated summary (recomputed on every line change) ────
    total_parts_cost = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    total_labor_cost = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    total_vat = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    total_discount = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    gross_amount = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    net_amount = db.Column(db.Numeric(18, 2), default=0, nullable=False)
    total_invoice_amount = db.Column(db.Numeric(18, 2), default=0, nullable=False)

    maintenance_order = db.relationship("MaintenanceOrder", backref="invoices")
    vendor = db.relationship("Vendor")
    requester = db.relationship(
        "User", primaryjoin="foreign(MaintenanceInvoice.requested_by) == User.id",
        viewonly=True)


class MaintenanceInvoiceLine(db.Model, BaseModel):
    __tablename__ = "maintenance_invoice_lines"

    invoice_id = db.Column(db.Integer, db.ForeignKey("maintenance_invoices.id"),
                           nullable=False, index=True)
    part_number = db.Column(db.String(60), nullable=True)
    part_description = db.Column(db.String(255), nullable=False)
    specification = db.Column(db.String(255), nullable=True)
    uom = db.Column(db.String(20), nullable=True)
    quantity = db.Column(db.Numeric(12, 2), nullable=False, default=1)
    unit_cost = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    discount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    line_amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    vat_amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(18, 2), nullable=False, default=0)
    # Lookup EXPENSE_CATEGORY: PARTS/LABOR/TIRES/BATTERY/OIL/LUBRICANTS/
    # EXTERNAL_SERVICES/TOWING/MISC
    expense_category = db.Column(db.String(20), nullable=False)
    # Lookup CHARGE_TO: COMPANY/DEPARTMENT/EMPLOYEE/INSURANCE_CLAIM/
    # WARRANTY_CLAIM/VENDOR_WARRANTY/ACCIDENT_CLAIM/OTHERS
    charged_to = db.Column(db.String(20), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    invoice = db.relationship("MaintenanceInvoice", backref="line_items")
