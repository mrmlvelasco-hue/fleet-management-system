"""Vehicle master model."""
from app.extensions import db
from app.core.models.base import BaseModel


class Vehicle(db.Model, BaseModel):
    __tablename__ = "vehicles"
    # Philippine LTO: conduction number first, plate assigned later
    plate_number = db.Column(db.String(20), unique=True, nullable=True,
                             index=True)
    conduction_number = db.Column(db.String(30), unique=True, nullable=True,
                                  index=True)
    chassis_number = db.Column(db.String(50), unique=True, nullable=True)
    engine_number = db.Column(db.String(50), unique=True, nullable=True)
    vehicle_type_id = db.Column(db.Integer,
                                db.ForeignKey("vehicle_types.id"),
                                nullable=False)
    brand = db.Column(db.String(80), nullable=False)
    model = db.Column(db.String(80), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    # PMS Master matching dimensions (all optional — narrows PM Template
    # matching further when set; see maintenance_config PMS-1).
    variant = db.Column(db.String(80), nullable=True)
    engine_type = db.Column(db.String(80), nullable=True)
    transmission = db.Column(db.String(40), nullable=True)
    current_engine_hours = db.Column(db.Integer, nullable=True)
    color = db.Column(db.String(50))
    fuel_type = db.Column(db.String(20))          # from Lookup FUEL_TYPE
    branch_id = db.Column(db.Integer,
                          db.ForeignKey("branches.id"), nullable=False)
    department_id = db.Column(db.Integer,
                              db.ForeignKey("departments.id"), nullable=True)
    business_unit_id = db.Column(db.Integer,
                                 db.ForeignKey("business_units.id"),
                                 nullable=True)
    assigned_driver_id = db.Column(db.Integer, db.ForeignKey("drivers.id"),
                                   nullable=True)
    # "Purchase Date" in the UI — column name kept as acquisition_date for
    # backward compatibility with existing data/reports.
    acquisition_date = db.Column(db.Date, nullable=True)
    acquisition_cost = db.Column(db.Numeric(18, 2), nullable=True)
    current_odometer = db.Column(db.Integer, default=0, nullable=False)

    # ── Vehicle Master enhancement (identifiers) ──────────────────────
    far_number = db.Column(db.String(60), nullable=True)
    cr_number = db.Column(db.String(60), nullable=True)
    mv_file_number = db.Column(db.String(60), nullable=True)
    remarks = db.Column(db.Text, nullable=True)

    # ── Classification lookups/free-text ──────────────────────────────
    vehicle_body_type = db.Column(db.String(40), nullable=True)  # Lookup VEHICLE_BODY_TYPE
    displacement = db.Column(db.String(40), nullable=True)
    component_group = db.Column(db.String(40), nullable=True)  # Lookup COMPONENT_GROUP
    supplier = db.Column(db.String(120), nullable=True)
    leasing_company = db.Column(db.String(120), nullable=True)

    # ── Financials ──────────────────────────────────────────────────
    top_up_amount = db.Column(db.Numeric(18, 2), nullable=True)  # VAT exclusive
    assured_value_current_year = db.Column(db.Numeric(18, 2), nullable=True)

    # ── Delivery / contract dates ──────────────────────────────────
    delivery_date = db.Column(db.Date, nullable=True)
    start_date = db.Column(db.Date, nullable=True)
    end_date = db.Column(db.Date, nullable=True)

    # ── Insurance ──────────────────────────────────────────────────
    insurance_reference_number = db.Column(db.String(60), nullable=True)
    comprehensive_policy_number = db.Column(db.String(60), nullable=True)
    comprehensive_insurance_provider = db.Column(db.String(120), nullable=True)
    ctpl_policy_number = db.Column(db.String(60), nullable=True)
    ctpl_insurance_provider = db.Column(db.String(120), nullable=True)
    lto_office = db.Column(db.String(120), nullable=True)

    has_ctpl = db.Column(db.Boolean, default=False, nullable=False)
    ctpl_from_date = db.Column(db.Date, nullable=True)
    ctpl_to_date = db.Column(db.Date, nullable=True)
    has_od_theft_aon = db.Column(db.Boolean, default=False, nullable=False)
    od_theft_aon_from_date = db.Column(db.Date, nullable=True)
    od_theft_aon_to_date = db.Column(db.Date, nullable=True)
    has_vtpl_pd = db.Column(db.Boolean, default=False, nullable=False)
    vtpl_pd_from_date = db.Column(db.Date, nullable=True)
    vtpl_pd_to_date = db.Column(db.Date, nullable=True)
    has_vtpl_bi = db.Column(db.Boolean, default=False, nullable=False)
    vtpl_bi_from_date = db.Column(db.Date, nullable=True)
    vtpl_bi_to_date = db.Column(db.Date, nullable=True)
    has_inland_marine = db.Column(db.Boolean, default=False, nullable=False)

    # ── Assignment classification ──────────────────────────────────
    # PRIMARY | SECONDARY
    assignment = db.Column(db.String(10), nullable=True)
    # CAR_PLAN | COMPANY_OWNED | OTHERS
    assignment_group_classification = db.Column(db.String(20), nullable=True)
    # SALES | NON_SALES
    vehicle_usage = db.Column(db.String(12), nullable=True)
    mr_eds = db.Column(db.Boolean, nullable=True)
    with_vehicle_contract = db.Column(db.Boolean, nullable=True)

    # Assigned PM Template — direct link to the specific PM interval that
    # applies to this vehicle (set at registration/edit). When set, this
    # takes precedence over any make/model or vehicle-type PM matching in
    # PMDueCalculationService, since the fleet admin has explicitly said
    # "this exact vehicle follows this exact PM plan."
    pm_schedule_id = db.Column(db.Integer, db.ForeignKey("pm_schedules.id"),
                               nullable=True)
    # ACTIVE | INACTIVE | IN_REPAIR | DISPOSED
    status = db.Column(db.String(20), default="ACTIVE", nullable=False)
    notes = db.Column(db.Text)

    vehicle_type = db.relationship("VehicleType")
    branch = db.relationship("Branch")
    department = db.relationship("Department")
    business_unit = db.relationship("BusinessUnit")
    pm_schedule = db.relationship("PMSchedule")
    assigned_driver = db.relationship("Driver")

    def compute_assured_value(self, as_of_date=None):
        """Assured Value formula: 10% depreciation per year (compounding),
        computed every year from Delivery Date, applied against
        Acquisition Cost. Returns None if either input is missing —
        there's nothing sensible to compute without both."""
        from datetime import date as _date
        from decimal import Decimal
        if not self.acquisition_cost or not self.delivery_date:
            return None
        as_of_date = as_of_date or _date.today()
        years_elapsed = (as_of_date.year - self.delivery_date.year) - (
            1 if (as_of_date.month, as_of_date.day) <
                (self.delivery_date.month, self.delivery_date.day) else 0)
        years_elapsed = max(0, years_elapsed)
        value = Decimal(str(self.acquisition_cost)) * (Decimal("0.9") ** years_elapsed)
        return value.quantize(Decimal("0.01"))
