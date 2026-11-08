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

    def get_visible(self, record_id, user):
        """Like get(), but returns None if `user` doesn't have visibility
        into this tire per organizational scope (branch_id = which
        warehouse/stock it belongs to)."""
        obj = db.session.get(Tire, record_id)
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
        q = Tire.query
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
            q = q.filter(company_filter(Tire, company_id))
        records = q.order_by(Tire.brand, Tire.serial_number).all()
        if user is None:
            return records
        from app.core.company_resolution import effective_company_id
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return [t for t in records
               if t.created_by == getattr(user, "id", None)
               or scope_svc.covers(user.id, branch_id=t.branch_id,
                                   company_id=effective_company_id(t))]

    def list_page(self, include_inactive=True, status=None, user=None,
                 branch_id=None, company_id=None, search=None,
                 tire_type=None, page=1, page_size=20):
        """One page of tires, filtered, scoped and sorted IN SQL.

        list() above loads every visible tire and filters visibility
        in Python -- fine for a dropdown, unusable for a fleet-sized
        Tires screen (measured at 254-313ms against 20,000 rows to
        show 20, per the 2026-11-08 performance audit). The org-scope
        predicate is expressed in SQL via
        UserOrgScopeService.sql_visibility_clause(), the same
        translation already proven on Vehicle.list_page().

        Returns (rows, total), with `total` counting the filtered set
        so a caller can render "showing 1-20 of N".
        """
        from sqlalchemy import or_
        from sqlalchemy.orm import joinedload
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        # Matches what the API's _serialise() accesses per row
        # (t.branch.name, t.vendor.name, t.vehicle.plate_number) --
        # without this, a page of 20 rows cost up to 3 extra lazy-load
        # queries each.
        query = Tire.query.options(
            joinedload(Tire.branch), joinedload(Tire.vendor),
            joinedload(Tire.vehicle))
        if not include_inactive:
            query = query.filter_by(is_active=True)
        if status:
            query = query.filter_by(status=status)
        if branch_id:
            query = query.filter_by(branch_id=branch_id)
        if company_id:
            from app.core.company_resolution import company_filter
            query = query.filter(company_filter(Tire, company_id))
        if tire_type:
            query = query.filter_by(tire_type=tire_type)
        if search:
            # Same four fields the Python filter in tires.py checked
            # (serial_number, brand, size, tire_type), plus the
            # branch's name via a correlated subquery, since Tire has
            # no direct branch-name column to match in a plain OR.
            from app.modules.master_data.org.models import Branch
            needle = f"%{search}%"
            query = query.filter(or_(
                Tire.serial_number.ilike(needle),
                Tire.brand.ilike(needle),
                Tire.size.ilike(needle),
                Tire.tire_type.ilike(needle),
                Tire.branch_id.in_(
                    db.session.query(Branch.id)
                    .filter(Branch.name.ilike(needle))),
            ))

        if user is not None:
            clause = UserOrgScopeService().sql_visibility_clause(
                user.id, Tire)
            if clause is not None:
                query = query.filter(clause)

        query = query.order_by(Tire.brand, Tire.serial_number)
        total = query.order_by(None).count()
        rows = query.limit(page_size).offset((page - 1) * page_size).all()
        return rows, total

    def summary(self, include_inactive=True, status=None, user=None,
               branch_id=None, company_id=None, search=None,
               tire_type=None):
        """Status-count KPIs over the filtered (but unpaginated) set,
        aggregated in SQL -- the same fix shape Fuel's summary()
        received, so the chips above the Tires table cost one COUNT
        query instead of materialising every matching row to sum them
        in Python."""
        from sqlalchemy import or_, func, case
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        query = Tire.query
        if not include_inactive:
            query = query.filter_by(is_active=True)
        if status:
            query = query.filter_by(status=status)
        if branch_id:
            query = query.filter_by(branch_id=branch_id)
        if company_id:
            from app.core.company_resolution import company_filter
            query = query.filter(company_filter(Tire, company_id))
        if tire_type:
            query = query.filter_by(tire_type=tire_type)
        if search:
            # Same four fields the Python filter in tires.py checked
            # (serial_number, brand, size, tire_type), plus the
            # branch's name via a correlated subquery, since Tire has
            # no direct branch-name column to match in a plain OR.
            from app.modules.master_data.org.models import Branch
            needle = f"%{search}%"
            query = query.filter(or_(
                Tire.serial_number.ilike(needle),
                Tire.brand.ilike(needle),
                Tire.size.ilike(needle),
                Tire.tire_type.ilike(needle),
                Tire.branch_id.in_(
                    db.session.query(Branch.id)
                    .filter(Branch.name.ilike(needle))),
            ))
        if user is not None:
            clause = UserOrgScopeService().sql_visibility_clause(
                user.id, Tire)
            if clause is not None:
                query = query.filter(clause)

        row = query.with_entities(
            func.count(Tire.id),
            func.sum(case((Tire.status == "IN_STOCK", 1), else_=0)),
            func.sum(case((Tire.status == "MOUNTED", 1), else_=0)),
            func.sum(case((Tire.status == "RETREADED", 1), else_=0)),
            func.sum(case((Tire.status == "DISPOSED", 1), else_=0)),
            func.sum(case((Tire.is_active.is_(True), 1), else_=0)),
        ).one()
        total, in_stock, mounted, retreaded, disposed, active = row
        return {
            "total": total or 0,
            "in_stock": in_stock or 0,
            "mounted": mounted or 0,
            "retreaded": retreaded or 0,
            "disposed": disposed or 0,
            "active": active or 0,
        }

    def deactivate(self, record_id):
        obj = db.session.get(Tire, record_id)
        if obj:
            obj.is_active = False
            obj.status = "DISPOSED"
            db.session.commit()
