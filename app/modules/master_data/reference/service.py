"""Services for reference master data."""
from app.extensions import db
from app.modules.master_data.org.service import DuplicateCodeError
from app.modules.master_data.reference.models import (
    VehicleType, MaintenanceType)


class _BaseRefService:
    model = None

    def get(self, record_id):
        return db.session.get(self.model, record_id)

    def list(self, include_inactive=False):
        q = self.model.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(self.model.name).all()

    def deactivate(self, record_id):
        obj = db.session.get(self.model, record_id)
        if obj:
            obj.is_active = False
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(self.model, record_id)
        if obj:
            obj.is_active = True
            db.session.commit()


class VehicleTypeService(_BaseRefService):
    model = VehicleType

    def create(self, code, name, category, **kwargs):
        if VehicleType.query.filter_by(code=code).first():
            raise DuplicateCodeError(
                f"Vehicle type code '{code}' already exists.")
        obj = VehicleType(code=code, name=name, category=category, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(VehicleType, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj


class MaintenanceTypeService(_BaseRefService):
    model = MaintenanceType

    def create(self, code, name, category, **kwargs):
        if MaintenanceType.query.filter_by(code=code).first():
            raise DuplicateCodeError(
                f"Maintenance type code '{code}' already exists.")
        obj = MaintenanceType(code=code, name=name,
                              category=category, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(MaintenanceType, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj
