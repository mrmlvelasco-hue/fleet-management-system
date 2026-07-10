"""Tire master service."""
from app.extensions import db
from app.modules.master_data.tire.models import Tire


class DuplicateSerialError(Exception):
    pass


class TireService:
    def create(self, serial_number, brand, size, tire_type,
               purchase_date=None, purchase_cost=None,
               vendor_id=None, **kwargs):
        if Tire.query.filter_by(serial_number=serial_number).first():
            raise DuplicateSerialError(
                f"Tire serial number '{serial_number}' already exists.")
        obj = Tire(serial_number=serial_number, brand=brand, size=size,
                   tire_type=tire_type, purchase_date=purchase_date,
                   purchase_cost=purchase_cost, vendor_id=vendor_id,
                   **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Tire, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def get(self, record_id):
        return db.session.get(Tire, record_id)

    def list(self, include_inactive=False, status=None):
        q = Tire.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(Tire.brand, Tire.serial_number).all()

    def deactivate(self, record_id):
        obj = db.session.get(Tire, record_id)
        if obj:
            obj.is_active = False
            obj.status = "DISPOSED"
            db.session.commit()
