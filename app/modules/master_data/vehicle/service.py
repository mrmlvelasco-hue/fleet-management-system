"""Vehicle master service.

Brand/Model validation: Vehicle.brand/Vehicle.model stay as plain strings
(for zero blast radius on existing templates, search services, and PM
Template matching — see the design spec), but are now validated against
the VehicleBrand/VehicleModel master tables:

- strict=True (used by the web form): rejects missing/unknown Brand or
  Model, and rejects a Model that doesn't belong to the selected Brand,
  with the exact friendly messages requested.
- strict=False (default — internal calls, CSV import, existing tests):
  get-or-create semantics, so programmatic callers keep working exactly as
  before while still backfilling the master tables for future
  standardization.
"""
from app.extensions import db
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle_brand.models import (
    VehicleBrand, VehicleModel)
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


class DuplicateVehicleError(Exception):
    pass


class BrandRequiredError(Exception):
    pass


class ModelRequiredError(Exception):
    pass


class InvalidBrandError(Exception):
    pass


class InvalidModelError(Exception):
    pass


class ModelBrandMismatchError(Exception):
    pass


def _resolve_brand_model(brand: str, model: str, strict: bool) -> tuple[str, str]:
    """Validate/normalize brand+model. Returns (brand_name, model_name) using
    the master data's canonical casing. Raises the friendly errors above."""
    brand = (brand or "").strip()
    model = (model or "").strip()

    if not brand:
        raise BrandRequiredError("Brand is required.")
    if not model:
        raise ModelRequiredError("Model is required.")

    brand_svc = VehicleBrandService()
    model_svc = VehicleModelService()
    brand_row = brand_svc.get_by_name(brand)

    if strict:
        if brand_row is None:
            raise InvalidBrandError(
                "Please select a valid Brand from the master list.")
        model_row = model_svc.get_by_name_and_brand(model, brand_row.id)
        if model_row is None:
            # Distinguish "model doesn't exist anywhere" from "model exists
            # but under a different brand" for the most helpful message.
            any_model = VehicleModel.query.filter(
                db.func.lower(VehicleModel.name) == model.lower(),
                VehicleModel.is_active.is_(True)).first()
            if any_model:
                raise ModelBrandMismatchError(
                    "Selected Model does not belong to the selected Brand.")
            raise InvalidModelError(
                "Please select a valid Model from the master list.")
        return brand_row.name, model_row.name

    # Non-strict (backward-compatible get-or-create for internal/CSV/test callers)
    if brand_row is None:
        brand_row = brand_svc.create(name=brand)
    model_row = model_svc.get_by_name_and_brand(model, brand_row.id)
    if model_row is None:
        model_row = model_svc.create(brand_id=brand_row.id, name=model)
    return brand_row.name, model_row.name


class VehicleService:
    def create(self, vehicle_type_id, brand, model, year,
               branch_id, conduction_number=None, plate_number=None,
               strict=False, **kwargs):
        if conduction_number and Vehicle.query.filter_by(
                conduction_number=conduction_number).first():
            raise DuplicateVehicleError(
                f"Conduction number '{conduction_number}' already exists.")
        if plate_number and Vehicle.query.filter_by(
                plate_number=plate_number).first():
            raise DuplicateVehicleError(
                f"Plate number '{plate_number}' already exists.")
        brand, model = _resolve_brand_model(brand, model, strict)
        obj = Vehicle(
            vehicle_type_id=vehicle_type_id, brand=brand, model=model,
            year=year, branch_id=branch_id,
            conduction_number=conduction_number,
            plate_number=plate_number, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, strict=False, **kwargs):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            if "brand" in kwargs or "model" in kwargs:
                brand = kwargs.pop("brand", obj.brand)
                model = kwargs.pop("model", obj.model)
                brand, model = _resolve_brand_model(brand, model, strict)
                obj.brand = brand
                obj.model = model
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def assign_plate(self, vehicle_id, plate_number):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.plate_number = plate_number
            db.session.commit()
        return obj

    def update_odometer(self, vehicle_id, reading):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.current_odometer = reading
            db.session.commit()
        return obj

    def assign_driver(self, vehicle_id, driver_id):
        obj = db.session.get(Vehicle, vehicle_id)
        if obj:
            obj.assigned_driver_id = driver_id
            db.session.commit()
        return obj

    def get(self, record_id, include_inactive=True):
        obj = db.session.get(Vehicle, record_id)
        if obj is None or (not include_inactive and not obj.is_active):
            return None
        return obj

    def list(self, include_inactive=False, branch_id=None):
        q = Vehicle.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        return q.order_by(Vehicle.brand, Vehicle.model).all()

    def deactivate(self, record_id):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            obj.is_active = False
            obj.status = "INACTIVE"
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            obj.is_active = True
            obj.status = "ACTIVE"
            db.session.commit()
