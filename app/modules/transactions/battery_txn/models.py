"""Battery Management transaction: mount/dismount/dispose events against a
Battery master record."""
from app.extensions import db
from app.core.models.base import BaseModel


class BatteryTransaction(db.Model, BaseModel):
    __tablename__ = "battery_transactions"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                                index=True)
    # NULL only while a REPLACE with a vendor-supplied battery awaits
    # approval (see TireTransaction.tire_id).
    battery_id = db.Column(db.Integer, db.ForeignKey("batteries.id"),
                           nullable=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                           nullable=True)  # NULL when dismounted to stock
    # MOUNT | DISMOUNT | DISPOSE | TEST
    action = db.Column(db.String(10), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    remarks = db.Column(db.Text, nullable=True)
    odometer_at_service = db.Column(db.Integer, nullable=True)
    # TEST readings: voltage and GOOD | WEAK | REPLACE.
    voltage = db.Column(db.Numeric(5, 2), nullable=True)
    test_result = db.Column(db.String(10), nullable=True)
    # Same cost / source / MO rules as TireTransaction.
    cost = db.Column(db.Numeric(18, 2), nullable=True)
    source = db.Column(db.String(12), nullable=False, default="MANUAL",
                       server_default="MANUAL")
    maintenance_order_id = db.Column(
        db.Integer, db.ForeignKey("maintenance_orders.id"), nullable=True,
        index=True)

    # REPLACE as one document -- see TireTransaction.
    removed_battery_id = db.Column(db.Integer, db.ForeignKey("batteries.id"),
                                   nullable=True)
    removal_reason = db.Column(db.String(40), nullable=True)
    removed_to = db.Column(db.String(15), nullable=True)
    install_source = db.Column(db.String(10), nullable=True)
    new_serial = db.Column(db.String(60), nullable=True)
    new_brand = db.Column(db.String(80), nullable=True)
    new_model = db.Column(db.String(80), nullable=True)
    new_warranty_months = db.Column(db.Integer, nullable=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                          nullable=True)
    technician = db.Column(db.String(120), nullable=True)
    labor_cost = db.Column(db.Numeric(18, 2), nullable=True)

    # DRAFT | COMPLETED | CANCELLED
    status = db.Column(db.String(12), default="DRAFT", nullable=False)

    requested_by = db.Column(db.Integer, db.ForeignKey("users.id"),
                             nullable=True)
    approval_instance_id = db.Column(
        db.Integer, db.ForeignKey("approval_instances.id"), nullable=True)

    battery = db.relationship("Battery", foreign_keys=[battery_id])
    removed_battery = db.relationship("Battery",
                                      foreign_keys=[removed_battery_id])
    vendor = db.relationship("Vendor")
    scope_items = db.relationship(
        "BatteryTransactionScopeItem",
        order_by="BatteryTransactionScopeItem.sort_order",
        cascade="all, delete-orphan")
    vehicle = db.relationship("Vehicle")
    requester = db.relationship("User", foreign_keys=[requested_by])
    approval_instance = db.relationship("ApprovalInstance")
    maintenance_order = db.relationship("MaintenanceOrder")


class BatteryTransactionScopeItem(db.Model, BaseModel):
    """Scope of Work on a standalone battery transaction."""
    __tablename__ = "battery_transaction_scope_items"
    transaction_id = db.Column(db.Integer,
                               db.ForeignKey("battery_transactions.id"),
                               nullable=False, index=True)
    activity_description = db.Column(db.String(255), nullable=False)
    is_done = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
