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
        return scope

    def list_for_user(self, user_id: int, include_inactive: bool = False) -> list:
        q = UserOrgScope.query.filter_by(user_id=user_id)
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def remove(self, scope_id: int) -> None:
        scope = db.session.get(UserOrgScope, scope_id)
        if scope:
            scope.is_active = False
            db.session.commit()

    def covers(self, user_id: int, branch_id: int = None,
              business_unit_id: int = None) -> bool:
        """Does this user hold a scope that covers the given branch/BU?
        No branch/BU passed at all → nothing to check → True (backward
        compatibility for approval instances with no recorded org context).
        """
        if branch_id is None and business_unit_id is None:
            return True

        scopes = self.list_for_user(user_id)
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
