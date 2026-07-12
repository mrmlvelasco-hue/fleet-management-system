"""Business rules for Vehicle Brand / Model master data — the standardized
list of valid values used to prevent free-text spelling variations and
duplicates in Vehicle.brand/Vehicle.model."""
from app.extensions import db
from app.modules.master_data.vehicle_brand.models import (
    VehicleBrand, VehicleModel)


class DuplicateBrandError(Exception):
    pass


class DuplicateModelError(Exception):
    pass


class VehicleBrandService:
    def get_by_name(self, name: str):
        if not name:
            return None
        return VehicleBrand.query.filter(
            db.func.lower(VehicleBrand.name) == name.strip().lower(),
            VehicleBrand.is_active.is_(True)).first()

    def create(self, name: str) -> VehicleBrand:
        if self.get_by_name(name):
            raise DuplicateBrandError(
                f"Brand '{name}' already exists in the master data.")
        brand = VehicleBrand(name=name.strip())
        db.session.add(brand)
        db.session.commit()
        return brand

    def update(self, brand_id: int, name: str):
        brand = db.session.get(VehicleBrand, brand_id)
        if brand is None:
            return None
        existing = self.get_by_name(name)
        if existing and existing.id != brand_id:
            raise DuplicateBrandError(
                f"Brand '{name}' already exists in the master data.")
        brand.name = name.strip()
        db.session.commit()
        return brand

    def list(self, include_inactive: bool = False):
        q = VehicleBrand.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(VehicleBrand.name).all()

    def deactivate(self, brand_id: int):
        brand = db.session.get(VehicleBrand, brand_id)
        if brand:
            brand.is_active = False
            db.session.commit()


class VehicleModelService:
    def get_by_name_and_brand(self, name: str, brand_id: int):
        if not name:
            return None
        return VehicleModel.query.filter(
            db.func.lower(VehicleModel.name) == name.strip().lower(),
            VehicleModel.brand_id == brand_id,
            VehicleModel.is_active.is_(True)).first()

    def create(self, brand_id: int, name: str) -> VehicleModel:
        if self.get_by_name_and_brand(name, brand_id):
            raise DuplicateModelError(
                f"Model '{name}' already exists for the selected Brand.")
        model = VehicleModel(brand_id=brand_id, name=name.strip())
        db.session.add(model)
        db.session.commit()
        return model

    def update(self, model_id: int, name: str):
        model = db.session.get(VehicleModel, model_id)
        if model is None:
            return None
        existing = self.get_by_name_and_brand(name, model.brand_id)
        if existing and existing.id != model_id:
            raise DuplicateModelError(
                f"Model '{name}' already exists for the selected Brand.")
        model.name = name.strip()
        db.session.commit()
        return model

    def list(self, brand_id: int = None, include_inactive: bool = False):
        q = VehicleModel.query
        if brand_id:
            q = q.filter_by(brand_id=brand_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(VehicleModel.name).all()

    def deactivate(self, model_id: int):
        model = db.session.get(VehicleModel, model_id)
        if model:
            model.is_active = False
            db.session.commit()
