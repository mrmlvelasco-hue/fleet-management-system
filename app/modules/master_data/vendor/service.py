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

    def assign_companies(self, record_id, company_ids):
        """Replace this vendor's full set of served Companies (Batch 7).
        Alongside branches/business_units, not instead of them -- an
        empty list clears ONLY this axis."""
        from app.modules.master_data.org.models import OrgCompany
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        obj.companies = (
            OrgCompany.query.filter(OrgCompany.id.in_(company_ids)).all()
            if company_ids else [])
        db.session.commit()
        return obj

    def assign_regions(self, record_id, region_ids):
        """Replace this vendor's full set of served Regions (Batch 7) --
        "serves all of Luzon" rather than one Company in it."""
        from app.modules.master_data.org.models import Region
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        obj.regions = (
            Region.query.filter(Region.id.in_(region_ids)).all()
            if region_ids else [])
        db.session.commit()
        return obj

    def assign_area_districts(self, record_id, area_district_ids):
        """Replace this vendor's full set of served Area/Districts
        (Batch 7)."""
        from app.modules.master_data.org.models import AreaDistrict
        obj = db.session.get(Vendor, record_id)
        if obj is None:
            return None
        obj.area_districts = (
            AreaDistrict.query.filter(
                AreaDistrict.id.in_(area_district_ids)).all()
            if area_district_ids else [])
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
        if not (vendor.branches or vendor.business_units
                or vendor.companies or vendor.regions
                or vendor.area_districts):
            return True
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        if any(scope_svc.covers(user.id, branch_id=b.id) for b in vendor.branches):
            return True
        if any(scope_svc.covers(user.id, business_unit_id=bu.id)
              for bu in vendor.business_units):
            return True
        # Batch 7: Company/Region/Area-District are alternatives to
        # Branch/BU, not a further narrowing -- a vendor matching ANY
        # one assigned axis is visible, same "any assignment is enough"
        # rule the two existing axes already follow.
        if any(scope_svc.covers_org(user.id, company_id=c.id)
              for c in vendor.companies):
            return True
        if any(scope_svc.covers_org(user.id, region_id=r.id)
              for r in vendor.regions):
            return True
        if any(scope_svc.covers_org(user.id, area_district_id=a.id)
              for a in vendor.area_districts):
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

    def _sql_visibility_clause(self, user_id: int):
        """SQL equivalent of `_is_visible()`'s many-to-many predicate --
        the 2026-11-09 audit's deferred item 2.1b, picked back up once
        Tire/Battery/Driver's simpler single-column shape was shipped.

        Unlike UserOrgScopeService.sql_visibility_clause() (branch_id/
        company_id columns), a Vendor can be scoped across FIVE
        independent axes at once (branches, business units, companies,
        regions, area-districts), each an "OR" alternative, and three
        of those five have RECIPROCAL coverage rules in
        UserOrgScopeService.covers_org() that are easy to drop by
        accident:
          - an ORG_COMPANY scope also covers that Company's own Region
            and Area-District (a vendor scoped to either is visible),
          - an ORG_REGION scope also covers every Company AND every
            Area-District filed under that Region,
          - an ORG_AREA_DISTRICT scope also covers that Area's own
            parent Region, and any Company filed under that Area.
        Every one of those is exercised by its own test in
        tests/unit/test_vendor_sql_pagination.py precisely because
        re-deriving this from covers_org() by reading the code (rather
        than against that test suite) is where a fix like this goes
        quietly wrong.

        Returns a SQLAlchemy clause to OR onto `Vendor.created_by ==
        user_id` (the override that already applies in list()/
        get_visible()), or None when the user is unrestricted (no
        scope rows, or a GLOBAL/COMPANY scope) -- the caller should
        skip filtering entirely in that case.
        """
        from sqlalchemy import and_, exists, or_, select
        from app.modules.master_data.org.models import AreaDistrict, OrgCompany
        from app.modules.master_data.vendor.models import (
            vendor_area_districts, vendor_branches, vendor_business_units,
            vendor_companies, vendor_regions)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)

        scope_svc = UserOrgScopeService()
        scopes = scope_svc.list_for_user(user_id)
        if not scopes:
            return None
        if any(sc.scope_type in ("GLOBAL", "COMPANY") for sc in scopes):
            return None

        direct_branch_ids = {sc.branch_id for sc in scopes
                             if sc.scope_type == "BRANCH" and sc.branch_id}
        direct_bu_ids = {sc.business_unit_id for sc in scopes
                        if sc.scope_type == "BUSINESS_UNIT"
                        and sc.business_unit_id}
        direct_company_ids = {sc.company_id for sc in scopes
                              if sc.scope_type == "ORG_COMPANY"
                              and sc.company_id}
        direct_region_ids = {sc.region_id for sc in scopes
                             if sc.scope_type == "ORG_REGION"
                             and sc.region_id}
        direct_area_ids = {sc.area_district_id for sc in scopes
                           if sc.scope_type == "ORG_AREA_DISTRICT"
                           and sc.area_district_id}

        # covers_org()'s reciprocal rules: a Company scope also covers
        # its OWN Region/Area; an Area scope also covers its OWN parent
        # Region. Resolved once, from the small scopes list, not per
        # vendor row.
        company_regions, company_areas = set(), set()
        for cid in direct_company_ids:
            r, a = scope_svc._company_org_ids(cid)
            if r:
                company_regions.add(r)
            if a:
                company_areas.add(a)
        area_regions = set()
        if direct_area_ids:
            for area in AreaDistrict.query.filter(
                    AreaDistrict.id.in_(direct_area_ids)).all():
                if area.region_id:
                    area_regions.add(area.region_id)

        clauses = [
            # No assignment on ANY of the five axes = serves everyone,
            # same "no org context = unrestricted" rule _is_visible()
            # applies first, before even looking at the user's scope.
            and_(
                ~exists(select(vendor_branches.c.vendor_id).where(
                    vendor_branches.c.vendor_id == Vendor.id)),
                ~exists(select(vendor_business_units.c.vendor_id).where(
                    vendor_business_units.c.vendor_id == Vendor.id)),
                ~exists(select(vendor_companies.c.vendor_id).where(
                    vendor_companies.c.vendor_id == Vendor.id)),
                ~exists(select(vendor_regions.c.vendor_id).where(
                    vendor_regions.c.vendor_id == Vendor.id)),
                ~exists(select(vendor_area_districts.c.vendor_id).where(
                    vendor_area_districts.c.vendor_id == Vendor.id)),
            )
        ]

        if direct_branch_ids:
            clauses.append(exists(select(vendor_branches.c.vendor_id).where(
                vendor_branches.c.vendor_id == Vendor.id,
                vendor_branches.c.branch_id.in_(direct_branch_ids))))

        if direct_bu_ids:
            clauses.append(exists(
                select(vendor_business_units.c.vendor_id).where(
                    vendor_business_units.c.vendor_id == Vendor.id,
                    vendor_business_units.c.business_unit_id.in_(
                        direct_bu_ids))))

        # Company axis: direct ORG_COMPANY match, OR the company's own
        # Region/Area-District is directly scoped (covers_org(company_id=)).
        company_or = []
        if direct_company_ids:
            company_or.append(OrgCompany.id.in_(direct_company_ids))
        if direct_region_ids:
            company_or.append(OrgCompany.region_id.in_(direct_region_ids))
        if direct_area_ids:
            company_or.append(
                OrgCompany.area_district_id.in_(direct_area_ids))
        if company_or:
            clauses.append(exists(
                select(vendor_companies.c.vendor_id)
                .join(OrgCompany, OrgCompany.id == vendor_companies.c.company_id)
                .where(vendor_companies.c.vendor_id == Vendor.id,
                      or_(*company_or))))

        # Region axis: direct ORG_REGION match, OR a directly-scoped
        # Company's own Region, OR a directly-scoped Area's own parent
        # Region (covers_org(region_id=)).
        region_ids_covered = direct_region_ids | company_regions | area_regions
        if region_ids_covered:
            clauses.append(exists(select(vendor_regions.c.vendor_id).where(
                vendor_regions.c.vendor_id == Vendor.id,
                vendor_regions.c.region_id.in_(region_ids_covered))))

        # Area-District axis: direct ORG_AREA_DISTRICT match, OR a
        # directly-scoped Company's own Area, OR the area's parent
        # Region is directly scoped (covers_org(area_district_id=) --
        # note this one does NOT fold in company_regions/area_regions,
        # only the user's own direct ORG_REGION scopes, mirroring
        # covers_org()'s own asymmetry between the region and area
        # branches).
        area_ids_covered = direct_area_ids | company_areas
        area_or = []
        if area_ids_covered:
            area_or.append(AreaDistrict.id.in_(area_ids_covered))
        if direct_region_ids:
            area_or.append(AreaDistrict.region_id.in_(direct_region_ids))
        if area_or:
            clauses.append(exists(
                select(vendor_area_districts.c.vendor_id)
                .join(AreaDistrict,
                     AreaDistrict.id == vendor_area_districts.c.area_district_id)
                .where(vendor_area_districts.c.vendor_id == Vendor.id,
                      or_(*area_or))))

        return or_(*clauses)

    def _filtered_query(self, user=None, search=None, vendor_type=None,
                        active=None):
        """The filter/scope portion shared by list_page() and
        summary() -- kept in one place so the paginated list and the
        KPI chips above it can never disagree about which vendors
        count, the same reason Fuel's and Tire's summary() reuse their
        list's own filtering rather than re-deriving it.

        `active` is a tri-state filter, not a plain include_inactive
        flag, because the existing API (/vendors) supports asking for
        ONLY inactive rows (active=0) as well as ONLY active (active=1)
        or both (omitted) -- unlike Tire/Battery/Driver, which never
        needed that distinction.
        """
        from sqlalchemy import func, or_

        query = Vendor.query
        if active is True:
            query = query.filter(Vendor.is_active.is_(True))
        elif active is False:
            query = query.filter(Vendor.is_active.is_(False))
        if vendor_type:
            query = query.filter(Vendor.vendor_type == vendor_type)
        if search:
            like = f"%{search.lower()}%"
            query = query.filter(or_(
                func.lower(Vendor.code).like(like),
                func.lower(Vendor.name).like(like),
                func.lower(Vendor.city).like(like),
                func.lower(Vendor.phone).like(like),
                func.lower(Vendor.contact_person).like(like),
                func.lower(Vendor.vendor_type).like(like),
            ))

        if user is not None:
            visibility = self._sql_visibility_clause(
                getattr(user, "id", None))
            if visibility is not None:
                query = query.filter(or_(
                    Vendor.created_by == user.id, visibility))
        return query

    def list_page(self, user=None, search=None, vendor_type=None,
                  active=None, sort="name", direction="asc",
                  page=1, page_size=20):
        """One page of vendors, filtered, scoped and sorted IN SQL.

        list() above loads every vendor and filters visibility in
        Python with _is_visible()'s many-to-many predicate -- which
        also means `vendor.branches` (and the other four association
        collections) gets lazy-loaded PER ROW, an N+1 the old /vendors
        endpoint paid on every single vendor, every request (measured
        at ~14s for 20,000 vendors). This does the same filtering as a
        SQL EXISTS against the five vendor_* association tables via
        _sql_visibility_clause(), so cost stops scaling with the
        vendor table's size.

        Returns (rows, total), same contract as the Tire/Battery/
        Driver/Vehicle list_page() methods.
        """
        from sqlalchemy import func

        query = self._filtered_query(
            user=user, search=search, vendor_type=vendor_type, active=active)
        total = query.with_entities(func.count(Vendor.id)).scalar() or 0

        sort_col = {"name": Vendor.name, "code": Vendor.code,
                   "city": Vendor.city}.get(sort, Vendor.name)
        query = query.order_by(
            sort_col.desc() if direction == "desc" else sort_col.asc())

        rows = (query.offset((page - 1) * page_size).limit(page_size).all())
        return rows, total

    def summary(self, user=None, search=None, vendor_type=None,
               active=None):
        """Status-count KPIs over the filtered (but unpaginated) set,
        aggregated in SQL -- the same fix shape Tire/Battery/Fuel's
        summary() received, so the chips above the Vendors table cost
        one aggregate query instead of materialising (and, worse,
        N+1-lazy-loading) every matching row to sum in Python."""
        from sqlalchemy import case, func

        query = self._filtered_query(
            user=user, search=search, vendor_type=vendor_type, active=active)
        row = query.with_entities(
            func.count(Vendor.id),
            func.sum(case((Vendor.is_active.is_(True), 1), else_=0)),
            func.sum(case((Vendor.vendor_type == "GOODS", 1), else_=0)),
            func.sum(case((Vendor.vendor_type == "SERVICES", 1), else_=0)),
            func.sum(case((Vendor.vendor_type == "BOTH", 1), else_=0)),
        ).first()
        total, active_n, goods, services, both = row
        return {
            "total": total or 0,
            "active": active_n or 0,
            "goods": goods or 0,
            "services": services or 0,
            "both": both or 0,
        }

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
