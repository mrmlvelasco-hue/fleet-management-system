"""Organizational-scope assignment and coverage checks for approval
eligibility — the core of F1 (org-scoped approval resolution). A Role
alone (e.g. "Fleet Manager") is never sufficient to determine an approver;
the acting user's scope must also cover the transaction's branch/business
unit, unless they hold COMPANY/GLOBAL scope."""
from app.extensions import db
from app.modules.user_management.models import UserOrgScope

VALID_SCOPE_TYPES = {"BRANCH", "BUSINESS_UNIT", "COMPANY", "GLOBAL"}


class InvalidScopeError(Exception):
    pass


class UserOrgScopeService:
    def assign(self, user_id: int, *, scope_type: str, branch_id: int = None,
               business_unit_id: int = None) -> UserOrgScope:
        if scope_type not in VALID_SCOPE_TYPES:
            raise InvalidScopeError(
                f"'{scope_type}' is not a valid scope type. Must be one "
                f"of: {', '.join(sorted(VALID_SCOPE_TYPES))}.")
        if scope_type == "BRANCH" and not branch_id:
            raise InvalidScopeError("BRANCH scope requires a branch_id.")
        if scope_type == "BUSINESS_UNIT" and not business_unit_id:
            raise InvalidScopeError(
                "BUSINESS_UNIT scope requires a business_unit_id.")

        scope = UserOrgScope(user_id=user_id, scope_type=scope_type,
                            branch_id=branch_id if scope_type == "BRANCH" else None,
                            business_unit_id=business_unit_id
                            if scope_type == "BUSINESS_UNIT" else None)
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

    def covers(self, user_id: int, branch_id: int = None,
              business_unit_id: int = None) -> bool:
        """Does this user hold a scope that covers the given branch/BU?

        No branch/BU passed at all → nothing to check → True (backward
        compatibility for approval instances with no recorded org
        context).

        User has been assigned NO scope rows at all → treated as not yet
        opted into org-scoping → True (backward compatibility: rolling out
        F1 must not silently lock out every existing approver who hasn't
        been explicitly assigned a scope yet — only users an admin has
        actually configured a UserOrgScope for become restricted to it).
        """
        if branch_id is None and business_unit_id is None:
            return True

        scopes = self.list_for_user(user_id)
        if not scopes:
            return True

        for scope in scopes:
            if scope.scope_type in ("GLOBAL", "COMPANY"):
                return True
            if (scope.scope_type == "BRANCH" and branch_id is not None
                    and scope.branch_id == branch_id):
                return True
            if (scope.scope_type == "BUSINESS_UNIT" and business_unit_id is not None
                    and scope.business_unit_id == business_unit_id):
                return True
        return False
