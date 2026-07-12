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

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this battery per organizational scope."""
        obj = db.session.get(Battery, record_id)
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

    def list(self, include_inactive=False, status=None, user=None):
        q = Battery.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if status:
            q = q.filter_by(status=status)
        records = q.order_by(Battery.brand, Battery.serial_number).all()
        if user is None:
            return records
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [b for b in records
               if b.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=b.branch_id)]

    def deactivate(self, record_id):
        obj = db.session.get(Battery, record_id)
        if obj:
            obj.is_active = False
            obj.status = "DISPOSED"
            db.session.commit()
