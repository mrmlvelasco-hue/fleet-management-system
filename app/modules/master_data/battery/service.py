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
        from app.core.company_resolution import effective_company_id
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        if UserOrgScopeService().covers(
                user.id, branch_id=obj.branch_id,
                company_id=effective_company_id(obj)):
            return obj
        return None

    def list(self, include_inactive=False, status=None, user=None,
             branch_id=None, company_id=None):
        q = Battery.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if status:
            q = q.filter_by(status=status)
        if branch_id:
            q = q.filter_by(branch_id=branch_id)
        if company_id:
            # Effective Company: own, else the branch's --
            # one shared definition, so this count and the
            # list screen can never disagree.
            from app.core.company_resolution import company_filter
            q = q.filter(company_filter(Battery, company_id))
        records = q.order_by(Battery.brand, Battery.serial_number).all()
        if user is None:
            return records
        from app.core.company_resolution import effective_company_id
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [b for b in records
               if b.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=b.branch_id,
                                   company_id=effective_company_id(b))]

    def list_page(self, include_inactive=True, status=None, user=None,
                 branch_id=None, company_id=None, search=None,
                 page=1, page_size=20):
        """One page of batteries, filtered, scoped and sorted IN SQL --
        the Battery twin of TireService.list_page(); see that
        docstring for the rationale (2026-11-08 performance audit,
        section 2.1)."""
        from sqlalchemy import or_
        from sqlalchemy.orm import joinedload
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        query = Battery.query.options(
            joinedload(Battery.branch), joinedload(Battery.vendor),
            joinedload(Battery.vehicle))
        if not include_inactive:
            query = query.filter_by(is_active=True)
        if status:
            query = query.filter_by(status=status)
        if branch_id:
            query = query.filter_by(branch_id=branch_id)
        if company_id:
            from app.core.company_resolution import company_filter
            query = query.filter(company_filter(Battery, company_id))
        if search:
            # Same fields the Python filter in batteries.py checked
            # (serial_number, brand, capacity_ah, voltage as text,
            # branch name) -- cast() reproduces the "stringify, then
            # substring-match" behaviour of the old Python join for
            # the two integer columns.
            from sqlalchemy import cast, String
            from app.modules.master_data.org.models import Branch
            needle = f"%{search}%"
            query = query.filter(or_(
                Battery.serial_number.ilike(needle),
                Battery.brand.ilike(needle),
                cast(Battery.capacity_ah, String).ilike(needle),
                cast(Battery.voltage, String).ilike(needle),
                Battery.branch_id.in_(
                    db.session.query(Branch.id)
                    .filter(Branch.name.ilike(needle))),
            ))

        if user is not None:
            clause = UserOrgScopeService().sql_visibility_clause(
                user.id, Battery)
            if clause is not None:
                query = query.filter(clause)

        query = query.order_by(Battery.brand, Battery.serial_number)
        total = query.order_by(None).count()
        rows = query.limit(page_size).offset((page - 1) * page_size).all()
        return rows, total

    def summary(self, include_inactive=True, status=None, user=None,
               branch_id=None, company_id=None, search=None):
        """Status-count KPIs over the filtered (but unpaginated) set,
        aggregated in SQL -- the Battery twin of TireService.summary()."""
        from sqlalchemy import or_, func, case
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        query = Battery.query
        if not include_inactive:
            query = query.filter_by(is_active=True)
        if status:
            query = query.filter_by(status=status)
        if branch_id:
            query = query.filter_by(branch_id=branch_id)
        if company_id:
            from app.core.company_resolution import company_filter
            query = query.filter(company_filter(Battery, company_id))
        if search:
            # Same fields the Python filter in batteries.py checked
            # (serial_number, brand, capacity_ah, voltage as text,
            # branch name) -- cast() reproduces the "stringify, then
            # substring-match" behaviour of the old Python join for
            # the two integer columns.
            from sqlalchemy import cast, String
            from app.modules.master_data.org.models import Branch
            needle = f"%{search}%"
            query = query.filter(or_(
                Battery.serial_number.ilike(needle),
                Battery.brand.ilike(needle),
                cast(Battery.capacity_ah, String).ilike(needle),
                cast(Battery.voltage, String).ilike(needle),
                Battery.branch_id.in_(
                    db.session.query(Branch.id)
                    .filter(Branch.name.ilike(needle))),
            ))
        if user is not None:
            clause = UserOrgScopeService().sql_visibility_clause(
                user.id, Battery)
            if clause is not None:
                query = query.filter(clause)

        row = query.with_entities(
            func.count(Battery.id),
            func.sum(case((Battery.status == "IN_STOCK", 1), else_=0)),
            func.sum(case((Battery.status == "MOUNTED", 1), else_=0)),
            func.sum(case((Battery.status == "DISPOSED", 1), else_=0)),
            func.sum(case((Battery.is_active.is_(True), 1), else_=0)),
        ).one()
        total, in_stock, mounted, disposed, active = row
        return {
            "total": total or 0,
            "in_stock": in_stock or 0,
            "mounted": mounted or 0,
            "disposed": disposed or 0,
            "active": active or 0,
        }

    def deactivate(self, record_id):
        obj = db.session.get(Battery, record_id)
        if obj:
            obj.is_active = False
            obj.status = "DISPOSED"
            db.session.commit()
