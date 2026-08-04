"""Fuel Management — models.

Built around one hard truth about fleet-card data: the odometer is typed
by a driver at a pump, so a meaningful share of readings are wrong
(transposed digits, a dropped digit, the litre figure typed into the
odometer box). A single bad reading corrupts the consumption figure for
TWO fills — the one it belongs to and the one after it — so consumption
computed naively from raw card data is not trustworthy.

The design consequence: a fuel transaction records the odometer the card
reported AND a separate, validated view of it. Money is always recorded;
consumption is only computed from readings that pass validation. That
keeps the financial record complete (the fuel was genuinely bought)
without letting a typo silently poison efficiency reporting.
"""
from app.extensions import db
from app.core.models.base import BaseModel


class FuelCard(db.Model, BaseModel):
    """A fleet card (e.g. Petron), assigned to a vehicle or a driver.

    Assignment is deliberately either/or rather than mandatory-both:
    some cards follow the vehicle, some follow the driver, and forcing
    one model onto both is how a card ends up mis-assigned.
    """
    __tablename__ = "fuel_cards"

    card_number = db.Column(db.String(50), nullable=False, unique=True)
    provider = db.Column(db.String(80), nullable=True)        # Petron, Shell...
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                          nullable=True, index=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                         nullable=True, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                         nullable=True)
    issued_date = db.Column(db.Date, nullable=True)
    expiry_date = db.Column(db.Date, nullable=True)
    monthly_limit = db.Column(db.Numeric(18, 2), nullable=True)
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)
    remarks = db.Column(db.String(255), nullable=True)

    vehicle = db.relationship("Vehicle", lazy="joined")


class FuelTransaction(db.Model, BaseModel):
    """One fill.

    `odometer_reported` is what the card/driver said. `odometer_used` is
    what the system is willing to compute consumption from — either the
    same value, a corrected one, or NULL when no trustworthy reading is
    available. Keeping both means a correction never destroys the
    original record, which matters when reconciling against a supplier
    statement.
    """
    __tablename__ = "fuel_transactions"

    document_number = db.Column(db.String(40), unique=True, nullable=True,
                               index=True)
    vehicle_id = db.Column(db.Integer, db.ForeignKey("vehicles.id"),
                          nullable=False, index=True)
    driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                         nullable=True)
    fuel_card_id = db.Column(db.Integer, db.ForeignKey("fuel_cards.id"),
                            nullable=True, index=True)
    vendor_id = db.Column(db.Integer, db.ForeignKey("vendors.id"),
                         nullable=True)

    transaction_date = db.Column(db.DateTime, nullable=False, index=True)
    station = db.Column(db.String(120), nullable=True)
    fuel_type = db.Column(db.String(30), nullable=True)

    litres = db.Column(db.Numeric(12, 3), nullable=False)
    price_per_litre = db.Column(db.Numeric(12, 4), nullable=True)
    total_amount = db.Column(db.Numeric(18, 2), nullable=False)

    # As reported by the card / driver -- never altered.
    odometer_reported = db.Column(db.Integer, nullable=True)
    # What consumption may be computed from. NULL means "not trustworthy".
    odometer_used = db.Column(db.Integer, nullable=True)

    # OK | SUSPECT | CORRECTED | MISSING -- see OdometerValidationService.
    odometer_status = db.Column(db.String(15), default="OK", nullable=False,
                               index=True)
    odometer_note = db.Column(db.String(255), nullable=True)
    # Set when a PERSON has confirmed a flagged reading is genuinely
    # correct (a replaced instrument cluster, a real long trip).
    # Re-validation must never overturn that -- otherwise correcting an
    # early fill silently discards every human decision after it, which
    # is exactly the bug this flag was added to fix.
    odometer_confirmed = db.Column(db.Boolean, default=False, nullable=False)

    # Computed on validation, from the previous trustworthy fill.
    distance_km = db.Column(db.Integer, nullable=True)
    km_per_litre = db.Column(db.Numeric(10, 3), nullable=True)
    cost_per_km = db.Column(db.Numeric(12, 4), nullable=True)

    # Flags raised by anomaly detection (comma-separated codes).
    anomaly_flags = db.Column(db.String(255), nullable=True)

    reference_number = db.Column(db.String(60), nullable=True)
    source = db.Column(db.String(20), default="MANUAL", nullable=False)
    remarks = db.Column(db.String(255), nullable=True)

    vehicle = db.relationship("Vehicle", lazy="joined")
    fuel_card = db.relationship("FuelCard", lazy="joined")

    __table_args__ = (
        # A fleet-card statement re-imported (very common when reconciling)
        # must not double-count the same fill.
        db.UniqueConstraint("vehicle_id", "transaction_date",
                           "reference_number",
                           name="uq_fuel_txn_vehicle_date_ref"),
    )
