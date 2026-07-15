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


class InvalidVehicleDataError(Exception):
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


def _validate_business_rules(data: dict) -> None:
    """Cross-field validations that apply on both create() and update() —
    checked against the merged field set (existing values + incoming
    kwargs), since update() may only pass a subset of fields."""
    cost = data.get("acquisition_cost")
    if cost is not None and cost != "":
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            raise InvalidVehicleDataError("Acquisition Cost must be a valid number.")
        if cost <= 0:
            raise InvalidVehicleDataError("Acquisition Cost must be greater than zero.")

    purchase = data.get("acquisition_date")
    delivery = data.get("delivery_date")
    if purchase and delivery and purchase > delivery:
        raise InvalidVehicleDataError(
            "Purchase Date cannot be later than Delivery Date.")

    for label, from_field, to_field in [
        ("CTPL", "ctpl_from_date", "ctpl_to_date"),
        ("OD/THEFT/AON", "od_theft_aon_from_date", "od_theft_aon_to_date"),
        ("VTPL/PD", "vtpl_pd_from_date", "vtpl_pd_to_date"),
        ("VTPL/BI", "vtpl_bi_from_date", "vtpl_bi_to_date"),
    ]:
        from_date, to_date = data.get(from_field), data.get(to_field)
        if from_date and to_date and from_date >= to_date:
            raise InvalidVehicleDataError(
                f"{label} 'From Date' must be earlier than 'To Date'.")


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
        engine_number = kwargs.get("engine_number")
        if engine_number and Vehicle.query.filter_by(
                engine_number=engine_number).first():
            raise DuplicateVehicleError(
                f"Engine number '{engine_number}' already exists.")
        _validate_business_rules(kwargs)
        brand, model = _resolve_brand_model(brand, model, strict)
        obj = Vehicle(
            vehicle_type_id=vehicle_type_id, brand=brand, model=model,
            year=year, branch_id=branch_id,
            conduction_number=conduction_number,
            plate_number=plate_number, **kwargs)
        db.session.add(obj)
        self._commit_or_raise_friendly(conduction_number, plate_number,
                                       engine_number)
        return obj

    def _commit_or_raise_friendly(self, conduction_number, plate_number,
                                  engine_number=None):
        """Commits, translating any unique-constraint violation the
        pre-check couldn't catch (e.g. a race between two near-
        simultaneous requests) into the same friendly DuplicateVehicleError
        — a technical database error must never reach the end user."""
        from sqlalchemy.exc import IntegrityError
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            message = str(getattr(exc, "orig", exc)).lower()
            if plate_number and "plate_number" in message:
                raise DuplicateVehicleError(
                    f"Plate number '{plate_number}' already exists.")
            if conduction_number and "conduction_number" in message:
                raise DuplicateVehicleError(
                    f"Conduction number '{conduction_number}' already exists.")
            if engine_number and "engine_number" in message:
                raise DuplicateVehicleError(
                    f"Engine number '{engine_number}' already exists.")
            raise DuplicateVehicleError(
                "This vehicle could not be saved because one of its unique "
                "fields (Plate Number, Conduction Number, Chassis Number, "
                "or Engine Number) is already used by another vehicle. "
                "Please check and correct the highlighted field.")

    def update(self, record_id, strict=False, **kwargs):
        obj = db.session.get(Vehicle, record_id)
        if obj:
            engine_number = kwargs.get("engine_number")
            if engine_number and engine_number != obj.engine_number:
                existing = Vehicle.query.filter_by(
                    engine_number=engine_number).first()
                if existing and existing.id != obj.id:
                    raise DuplicateVehicleError(
                        f"Engine number '{engine_number}' already exists.")
            merged = {
                "acquisition_cost": kwargs.get("acquisition_cost", obj.acquisition_cost),
                "acquisition_date": kwargs.get("acquisition_date", obj.acquisition_date),
                "delivery_date": kwargs.get("delivery_date", obj.delivery_date),
                "ctpl_from_date": kwargs.get("ctpl_from_date", obj.ctpl_from_date),
                "ctpl_to_date": kwargs.get("ctpl_to_date", obj.ctpl_to_date),
                "od_theft_aon_from_date": kwargs.get("od_theft_aon_from_date", obj.od_theft_aon_from_date),
                "od_theft_aon_to_date": kwargs.get("od_theft_aon_to_date", obj.od_theft_aon_to_date),
                "vtpl_pd_from_date": kwargs.get("vtpl_pd_from_date", obj.vtpl_pd_from_date),
                "vtpl_pd_to_date": kwargs.get("vtpl_pd_to_date", obj.vtpl_pd_to_date),
                "vtpl_bi_from_date": kwargs.get("vtpl_bi_from_date", obj.vtpl_bi_from_date),
                "vtpl_bi_to_date": kwargs.get("vtpl_bi_to_date", obj.vtpl_bi_to_date),
            }
            _validate_business_rules(merged)
            if "brand" in kwargs or "model" in kwargs:
                brand = kwargs.pop("brand", obj.brand)
                model = kwargs.pop("model", obj.model)
                brand, model = _resolve_brand_model(brand, model, strict)
                obj.brand = brand
                obj.model = model
            for k, v in kwargs.items():
                setattr(obj, k, v)
            self._commit_or_raise_friendly(
                kwargs.get("conduction_number"), kwargs.get("plate_number"),
                engine_number)
        return obj

    def get_clone_data(self, record_id) -> dict:
        """Returns a dict of this vehicle's field values suitable for
        pre-filling a NEW vehicle form — every unique identifier (plate,
        conduction, chassis, engine numbers) is deliberately excluded so
        the clone can't collide with the original."""
        obj = db.session.get(Vehicle, record_id)
        if obj is None:
            return {}
        exclude = {"id", "plate_number", "conduction_number",
                  "chassis_number", "engine_number", "created_at",
                  "updated_at", "created_by", "updated_by"}
        return {c.name: getattr(obj, c.name)
               for c in obj.__table__.columns if c.name not in exclude}

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

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this vehicle per organizational scope — mirrors the same
        pattern used for transaction detail pages."""
        obj = db.session.get(Vehicle, record_id)
        if obj is None:
            return None
        if user is None:
            return obj
        if obj.created_by == getattr(user, "id", None):
            return obj
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        if UserOrgScopeService().covers(user.id, branch_id=obj.branch_id):
            return obj
        return None

    def list(self, include_inactive=False, branch_id=None, user=None):
        q = Vehicle.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        records = q.order_by(Vehicle.brand, Vehicle.model).all()
        if user is None:
            return records
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [v for v in records
               if v.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=v.branch_id)]

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
