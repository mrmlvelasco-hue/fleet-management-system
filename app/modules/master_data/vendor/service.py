"""Vendor master service."""
from app.extensions import db
from app.modules.master_data.vendor.models import Vendor, VendorContact


class DuplicateCodeError(Exception):
    pass


class VendorContactService:
    def create(self, *, vendor_id, contact_name, tel_number=None,
              cel_number=None, email=None, position=None):
        contact = VendorContact(
            vendor_id=vendor_id, contact_name=contact_name,
            tel_number=tel_number, cel_number=cel_number,
            email=email, position=position)
        db.session.add(contact)
        db.session.commit()
        return contact

    def list_for_vendor(self, vendor_id) -> list:
        return (VendorContact.query
               .filter_by(vendor_id=vendor_id, is_active=True)
               .order_by(VendorContact.id).all())

    def delete(self, contact_id):
        contact = db.session.get(VendorContact, contact_id)
        if contact:
            contact.is_active = False
            db.session.commit()


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

    def assign_branches(self, record_id, branch_ids):
        """Replace this vendor's full set of served branches. An empty
        list means "serves everyone" (no restriction)."""
        from app.modules.master_data.org.models import Branch
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        obj.branches = Branch.query.filter(Branch.id.in_(branch_ids)).all() \
            if branch_ids else []
        db.session.commit()
        return obj

    def assign_business_units(self, record_id, business_unit_ids):
        """Replace this vendor's full set of served business units."""
        from app.modules.master_data.org.models import BusinessUnit
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        obj.business_units = (
            BusinessUnit.query.filter(BusinessUnit.id.in_(business_unit_ids)).all()
            if business_unit_ids else [])
        db.session.commit()
        return obj

    def get(self, record_id):
        return db.session.get(Vendor, record_id)

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this vendor. A vendor with no branch/BU assignments at all
        serves everyone (e.g. a nationwide supplier) — only vendors
        explicitly scoped to specific branches/BUs get restricted."""
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        if user is None:
            return obj
        if obj.created_by == getattr(user, "id", None):
            return obj
        if self._is_visible(obj, user):
            return obj
        return None

    def _is_visible(self, vendor, user) -> bool:
        if not vendor.branches and not vendor.business_units:
            return True
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        if any(scope_svc.covers(user.id, branch_id=b.id) for b in vendor.branches):
            return True
        if any(scope_svc.covers(user.id, business_unit_id=bu.id)
              for bu in vendor.business_units):
            return True
        return False

    def list(self, include_inactive=False, user=None):
        q = Vendor.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        records = q.order_by(Vendor.name).all()
        if user is None:
            return records
        return [v for v in records
               if v.created_by == getattr(user, "id", None)
               or self._is_visible(v, user)]

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
