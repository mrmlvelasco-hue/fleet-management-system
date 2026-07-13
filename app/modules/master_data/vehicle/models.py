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
    engine_number = db.Column(db.String(50), nullable=True)
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
    acquisition_date = db.Column(db.Date, nullable=True)
    acquisition_cost = db.Column(db.Numeric(18, 2), nullable=True)
    current_odometer = db.Column(db.Integer, default=0, nullable=False)
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
