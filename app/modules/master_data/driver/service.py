"""Driver/Assignee master service."""
from datetime import date, timedelta

from app.extensions import db
from app.modules.master_data.driver.models import Driver


class DuplicateDriverError(Exception):
    pass


class DriverService:
    def create(self, employee_number, first_name, last_name,
               license_number, license_expiry, license_type,
               branch_id, **kwargs):
        if Driver.query.filter_by(
                license_number=license_number).first():
            raise DuplicateDriverError(
                f"License number '{license_number}' already exists.")
        if Driver.query.filter_by(
                employee_number=employee_number).first():
            raise DuplicateDriverError(
                f"Employee number '{employee_number}' already exists.")
        obj = Driver(
            employee_number=employee_number, first_name=first_name,
            last_name=last_name, license_number=license_number,
            license_expiry=license_expiry, license_type=license_type,
            branch_id=branch_id, **kwargs)
        db.session.add(obj)
        db.session.commit()
        return obj

    def update(self, record_id, **kwargs):
        obj = db.session.get(Driver, record_id)
        if obj:
            for k, v in kwargs.items():
                setattr(obj, k, v)
            db.session.commit()
        return obj

    def get(self, record_id, include_inactive=True):
        return db.session.get(Driver, record_id)

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this driver per organizational scope."""
        obj = db.session.get(Driver, record_id)
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
        q = Driver.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        records = q.order_by(Driver.last_name, Driver.first_name).all()
        if user is None:
            return records
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [d for d in records
               if d.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=d.branch_id)]

    def get_expiring_licenses(self, days=30):
        """Drivers whose license expires within `days` days."""
        threshold = date.today() + timedelta(days=days)
        return (Driver.query
                .filter(Driver.is_active.is_(True),
                        Driver.license_expiry <= threshold)
                .order_by(Driver.license_expiry)
                .all())

    def deactivate(self, record_id):
        obj = db.session.get(Driver, record_id)
        if obj:
            obj.is_active = False
            obj.status = "INACTIVE"
            db.session.commit()

    def reactivate(self, record_id):
        obj = db.session.get(Driver, record_id)
        if obj:
            obj.is_active = True
            obj.status = "ACTIVE"
            db.session.commit()
