"""Battery master service."""
from app.extensions import db
from app.modules.master_data.battery.models import Battery


class DuplicateSerialError(Exception):
    pass


class BatteryService:
    def create(self, serial_number, brand, capacity_ah=None, voltage=None,
               purchase_date=None, purchase_cost=None, vendor_id=None,
               **kwargs):
        if Battery.query.filter_by(serial_number=serial_number).first():
            raise DuplicateSerialError(
                f"Battery serial number '{serial_number}' already exists.")
        obj = Battery(serial_number=serial_number, brand=brand,
                      capacity_ah=capacity_ah, voltage=voltage,
                      purchase_date=purchase_date,
                      purchase_cost=purchase_cost,
                      vendor_id=vendor_id, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Battery, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def get(self, record_id):
        return db.session.get(Battery, record_id)

    def list(self, include_inactive=False, status=None):
        q = Battery.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(Battery.brand, Battery.serial_number).all()

    def deactivate(self, record_id):
        obj = db.session.get(Battery, record_id)
        if obj:
            obj.is_active = False
            obj.status = "DISPOSED"
            db.session.commit()
