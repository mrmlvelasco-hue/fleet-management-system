"""Vendor master service."""
from app.extensions import db
from app.modules.master_data.vendor.models import Vendor


class DuplicateCodeError(Exception):
    pass


class VendorService:
    def create(self, code, name, vendor_type="GOODS", **kwargs):
        if Vendor.query.filter_by(code=code).first():
            raise DuplicateCodeError(f"Vendor code '{code}' already exists.")
        obj = Vendor(code=code, name=name, vendor_type=vendor_type, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Vendor, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def get(self, record_id):
        return db.session.get(Vendor, record_id)

    def list(self, include_inactive=False):
        q = Vendor.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.order_by(Vendor.name).all()

    def deactivate(self, record_id):
        obj = db.session.get(Vendor, record_id)
        if obj:
            obj.is_active = False
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(Vendor, record_id)
        if obj:
            obj.is_active = True
            db.session.commit()
