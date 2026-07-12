"""Vehicle Brand and Model master data — the configurable list of valid
values used to standardize Vehicle.brand/Vehicle.model entries (which stay
plain strings for backward compatibility with existing templates, search
services, PM Template matching, and CSV import — see the design spec)."""
from app.extensions import db
from app.core.models.base import BaseModel


class VehicleBrand(db.Model, BaseModel):
    __tablename__ = "vehicle_brands"
    name = db.Column(db.String(80), unique=True, nullable=False, index=True)

    models = db.relationship("VehicleModel", backref="brand",
                             order_by="VehicleModel.name")


class VehicleModel(db.Model, BaseModel):
    __tablename__ = "vehicle_models"
    brand_id = db.Column(db.Integer, db.ForeignKey("vehicle_brands.id"),
                         nullable=False)
    name = db.Column(db.String(80), nullable=False)

    __table_args__ = (
        db.UniqueConstraint("brand_id", "name", name="uq_brand_model_name"),
    )
