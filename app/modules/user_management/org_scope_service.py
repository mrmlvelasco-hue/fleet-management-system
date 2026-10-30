"""Organizational-scope assignment and coverage checks for approval
eligibility — the core of F1 (org-scoped approval resolution). A Role
alone (e.g. "Fleet Manager") is never sufficient to determine an approver;
the acting user's scope must also cover the transaction's branch/business
unit, unless they hold COMPANY/GLOBAL scope."""
from app.extensions import db
from app.modules.user_management.models import UserOrgScope

VALID_SCOPE_TYPES = {"BRANCH", "BUSINESS_UNIT", "COMPANY", "GLOBAL",
                     "ORG_COMPANY", "ORG_REGION", "ORG_AREA_DISTRICT"}

# Stage 3B: these three are the real per-company scopes, kept deliberately
# separate from the legacy "COMPANY" value (which means unrestricted, same
# as GLOBAL -- see UserOrgScope's docstring). Covering any of these means
# the scope narrows the user to one Company/Region/Area-District rather
# than opening everything up.
ORG_SCOPE_TYPES = {"ORG_COMPANY", "ORG_REGION", "ORG_AREA_DISTRICT"}


class InvalidScopeError(Exception):
    pass


class UserOrgScopeService:
    def assign(self, user_id: int, *, scope_type: str, branch_id: int = None,
               business_unit_id: int = None, company_id: int = None,
               region_id: int = None, area_district_id: int = None
               ) -> UserOrgScope:
        if scope_type not in VALID_SCOPE_TYPES:
            raise InvalidScopeError(
                f"'{scope_type}' is not a valid scope type. Must be one "
                f"of: {', '.join(sorted(VALID_SCOPE_TYPES))}.")
        if scope_type == "BRANCH" and not branch_id:
            raise InvalidScopeError("BRANCH scope requires a branch_id.")
        if scope_type == "BUSINESS_UNIT" and not business_unit_id:
            raise InvalidScopeError(
                "BUSINESS_UNIT scope requires a business_unit_id.")
        if scope_type == "ORG_COMPANY" and not company_id:
            raise InvalidScopeError(
                "ORG_COMPANY scope requires a company_id.")
        if scope_type == "ORG_REGION" and not region_id:
            raise InvalidScopeError(
                "ORG_REGION scope requires a region_id.")
        if scope_type == "ORG_AREA_DISTRICT" and not area_district_id:
            raise InvalidScopeError(
                "ORG_AREA_DISTRICT scope requires an area_district_id.")

        scope = UserOrgScope(
            user_id=user_id, scope_type=scope_type,
            branch_id=branch_id if scope_type == "BRANCH" else None,
            business_unit_id=business_unit_id
            if scope_type == "BUSINESS_UNIT" else None,
            company_id=company_id if scope_type == "ORG_COMPANY" else None,
            region_id=region_id if scope_type == "ORG_REGION" else None,
            area_district_id=area_district_id
            if scope_type == "ORG_AREA_DISTRICT" else None)
        db.session.add(scope)
        db.session.commit()
        # A request that GRANTS a scope and then re-reads it must not
        # see the answer it cached moments earlier.
        self.invalidate_cache()
        return scope

    def list_for_user(self, user_id: int, include_inactive: bool = False) -> list:
        """A user's org scopes, memoised for the life of the request.

        covers() calls this on EVERY invocation, and covers() is called
        once per row when filtering a list by org scope. Measured on the
        dashboard at 5,000 vehicles: 6,589 identical queries returning
        the same handful of rows -- 99% of every query the dashboard
        issued, and about two seconds of it.

        Stored on `flask.g` AND cleared by a teardown_request hook
        registered in the app factory.

        Both halves are required, and the second is easy to miss:
        flask.g is scoped to the APP context, not the request. Under a
        long-lived app context -- a Celery worker, or a test that pushes
        one around several requests -- g survives from one request to
        the next, so a scope revoked between them would still read as
        granted. A test caught exactly that. The teardown hook is what
        makes the lifetime actually per-request.

        Outside a request context (CLI, Celery task bodies) it falls
        straight through to the query rather than caching at all.

        include_inactive is part of the key: the two answers differ, and
        sharing one slot would let a maintenance screen asking for
        inactive rows poison the security check that must not see them.
        """
        def _query():
            q = UserOrgScope.query.filter_by(user_id=user_id)
            if not include_inactive:
                q = q.filter_by(is_active=True)
            return q.all()

        try:
            from flask import g, has_request_context
            if not has_request_context():
                return _query()
        except Exception:
            return _query()

        cache = getattr(g, "_fms_org_scopes", None)
        if cache is None:
            cache = {}
            g._fms_org_scopes = cache
        key = (user_id, include_inactive)
        if key not in cache:
            cache[key] = _query()
        return cache[key]

    @staticmethod
    def invalidate_cache():
        """Drop the per-request memo.

        Called after a scope is added or removed so a request that
        CHANGES scopes and then re-reads them does not see the stale
        answer it cached moments earlier.
        """
        try:
            from flask import g, has_request_context
            if has_request_context() and hasattr(g, "_fms_org_scopes"):
                del g._fms_org_scopes
        except Exception:
            pass

    def remove(self, scope_id: int) -> None:
        scope = db.session.get(UserOrgScope, scope_id)
        if scope:
            scope.is_active = False
            db.session.commit()
            self.invalidate_cache()

    def allowed_branch_ids(self, user_id: int):
        """The branch ids a user's own scope restricts them to, for a
        plain "scope this query by branch" join -- the same rule every
        module that does this (Vehicle.list(), the dashboards, Data
        Quality) already applies inline, pulled out once so a module
        that has NOT been scoped yet (Fuel, at the time this was
        added) can reuse it rather than re-deriving it.

        Returns None when the user is UNRESTRICTED -- no scope rows at
        all (not yet opted into org-scoping, same backward-compatible
        default as covers()), or a GLOBAL/COMPANY scope -- meaning
        "don't filter". Returns a (possibly empty) list of branch ids
        otherwise; an empty list means "no branch this user can see",
        not "unrestricted".
        """
        scopes = self.list_for_user(user_id)
        if not scopes:
            return None
        if any(sc.scope_type in ("GLOBAL", "COMPANY") for sc in scopes):
            return None
        return [sc.branch_id for sc in scopes
               if sc.scope_type == "BRANCH" and sc.branch_id is not None]

    @staticmethod
    def _company_org_ids(company_id: int):
        """The (region_id, area_district_id) the given Company is filed
        under, for resolving ORG_REGION/ORG_AREA_DISTRICT coverage — a
        scope holder never needs to know every Company in their Region,
        just the Region itself. Returns (None, None) if the company_id
        does not resolve (defensive; should not happen in practice)."""
        from app.modules.master_data.org.models import OrgCompany
        company = db.session.get(OrgCompany, company_id)
        if company is None:
            return None, None
        return company.region_id, company.area_district_id

    def covers_org(self, user_id: int, *, company_id: int = None,
                  region_id: int = None, area_district_id: int = None
                  ) -> bool:
        """Does this user's scope cover the given Company/Region/
        Area-District, given DIRECTLY rather than resolved from a
        transaction's company_id? covers() can only ask "does this
        scope cover Company X" (and derives X's region/area to check an
        ORG_REGION/ORG_AREA_DISTRICT scope) -- it has no way to ask
        "does this scope cover Region X" when nothing has told it which
        Company, which is exactly what a Vendor assigned directly to a
        Region/Area-District (Batch 7) needs to ask.

        Exactly one of the three kwargs is expected; passing none at
        all is the same backward-compatible "nothing to check" rule
        covers() uses. A broader user scope covers a narrower target
        (ORG_REGION covers a Company or Area/District inside it) and a
        narrower user scope covers a broader target it sits inside
        (ORG_COMPANY covers its own Region), so the match is symmetric
        around whichever of region/area the target resolves to.
        """
        if company_id is None and region_id is None \
                and area_district_id is None:
            return True

        scopes = self.list_for_user(user_id)
        if not scopes:
            return True

        target_region_id, target_area_id = region_id, area_district_id
        if company_id is not None:
            target_region_id, target_area_id = \
                self._company_org_ids(company_id)
        elif area_district_id is not None and target_region_id is None:
            from app.modules.master_data.org.models import AreaDistrict
            area = db.session.get(AreaDistrict, area_district_id)
            target_region_id = area.region_id if area else None

        for scope in scopes:
            if scope.scope_type in ("GLOBAL", "COMPANY"):
                return True
            if (scope.scope_type == "ORG_COMPANY" and company_id is not None
                    and scope.company_id == company_id):
                return True
            if (scope.scope_type == "ORG_AREA_DISTRICT"
                    and area_district_id is not None
                    and scope.area_district_id == area_district_id):
                return True
            if (scope.scope_type == "ORG_AREA_DISTRICT"
                    and target_area_id is not None
                    and scope.area_district_id == target_area_id):
                return True
            if (scope.scope_type == "ORG_COMPANY" and area_district_id is not None
                    and scope.company_id is not None):
                _, sc_area_id = self._company_org_ids(scope.company_id)
                if sc_area_id == area_district_id:
                    return True
            if (scope.scope_type == "ORG_REGION" and target_region_id is not None
                    and scope.region_id == target_region_id):
                return True
            if (scope.scope_type == "ORG_COMPANY" and region_id is not None
                    and scope.company_id is not None):
                sc_region_id, _ = self._company_org_ids(scope.company_id)
                if sc_region_id == region_id:
                    return True
            if (scope.scope_type == "ORG_AREA_DISTRICT" and region_id is not None
                    and scope.area_district_id is not None):
                from app.modules.master_data.org.models import AreaDistrict
                sc_area = db.session.get(AreaDistrict, scope.area_district_id)
                if sc_area is not None and sc_area.region_id == region_id:
                    return True
        return False

    def covers(self, user_id: int, branch_id: int = None,
              business_unit_id: int = None, company_id: int = None) -> bool:
        """Does this user hold a scope that covers the given branch/BU/
        Company?

        No context passed at all → nothing to check → True (backward
        compatibility for approval instances with no recorded org
        context).

        User has been assigned NO scope rows at all → treated as not yet
        opted into org-scoping → True (backward compatibility: rolling out
        F1 must not silently lock out every existing approver who hasn't
        been explicitly assigned a scope yet — only users an admin has
        actually configured a UserOrgScope for become restricted to it).
        """
        if branch_id is None and business_unit_id is None and company_id is None:
            return True

        scopes = self.list_for_user(user_id)
        if not scopes:
            return True

        company_region_id = company_area_id = None
        company_org_ids_resolved = False

        for scope in scopes:
            if scope.scope_type in ("GLOBAL", "COMPANY"):
                return True
            if (scope.scope_type == "BRANCH" and branch_id is not None
                    and scope.branch_id == branch_id):
                return True
            if (scope.scope_type == "BUSINESS_UNIT" and business_unit_id is not None
                    and scope.business_unit_id == business_unit_id):
                return True
            if scope.scope_type == "ORG_COMPANY" and company_id is not None \
                    and scope.company_id == company_id:
                return True
            if scope.scope_type in ("ORG_REGION", "ORG_AREA_DISTRICT") \
                    and company_id is not None:
                if not company_org_ids_resolved:
                    company_region_id, company_area_id = \
                        self._company_org_ids(company_id)
                    company_org_ids_resolved = True
                if scope.scope_type == "ORG_REGION" \
                        and scope.region_id == company_region_id:
                    return True
                if scope.scope_type == "ORG_AREA_DISTRICT" \
                        and scope.area_district_id == company_area_id:
                    return True
        return False
