"""Document Type and Numbering Scheme models."""
from app.extensions import db
from app.core.models.base import BaseModel


class DocumentType(db.Model, BaseModel):
    __tablename__ = "document_types"
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(255))
    requires_approval = db.Column(db.Boolean, default=False, nullable=False)
    auto_numbering = db.Column(db.Boolean, default=False, nullable=False)
    printable = db.Column(db.Boolean, default=False, nullable=False)
    mobile_available = db.Column(db.Boolean, default=False, nullable=False)
    attachment_allowed = db.Column(db.Boolean, default=False, nullable=False)

    numbering_scheme = db.relationship("NumberingScheme", uselist=False,
                                       backref="document_type")


class NumberingScheme(db.Model, BaseModel):
    __tablename__ = "numbering_schemes"
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"),
                                 unique=True, nullable=False)
    prefix = db.Column(db.String(20), default="", nullable=False)
    suffix = db.Column(db.String(20), default="", nullable=False)
    include_year = db.Column(db.Boolean, default=True, nullable=False)
    include_month = db.Column(db.Boolean, default=False, nullable=False)
    digit_count = db.Column(db.Integer, default=6, nullable=False)
    separator = db.Column(db.String(3), default="-", nullable=False)
    # NEVER | YEARLY | MONTHLY (string for MySQL/MSSQL portability)
    reset_policy = db.Column(db.String(10), default="YEARLY", nullable=False)

    counters = db.relationship("NumberingCounter", backref="scheme")


class NumberingCounter(db.Model, BaseModel):
    __tablename__ = "numbering_counters"
    scheme_id = db.Column(db.Integer, db.ForeignKey("numbering_schemes.id"),
                          nullable=False)
    year = db.Column(db.Integer, default=0, nullable=False)   # 0 = not scoped by year
    month = db.Column(db.Integer, default=0, nullable=False)  # 0 = not scoped by month
    last_number = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("scheme_id", "year", "month",
                            name="uq_counter_scope"),
    )
