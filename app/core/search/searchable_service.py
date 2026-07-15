"""Generic, paginated, multi-field search — the shared engine behind every
AJAX-backed "smart selector" dropdown across the system (Vehicles, Drivers,
Users, Vendors, and more to follow). Subclasses just declare `model`,
`search_fields`, and a `label()` formatter; no per-module search code.

Response shape matches Select2's AJAX contract directly, so the same
service powers both a plain JSON API and a Select2 dropdown with zero
translation layer.
"""
from app.extensions import db


class SearchableService:
    model = None            # subclass sets: the SQLAlchemy model
    search_fields = []       # subclass sets: list of column names to search
    sortable_fields = []     # subclass sets: whitelist of columns sortable in the Search Modal
    default_per_page = 20
    max_per_page = 50

    def label(self, obj) -> str:
        """Override to control the display text. Defaults to str(obj)."""
        return str(obj)

    def row(self, obj) -> dict:
        """Override to control the Search Modal table row shape.
        Defaults to {"id", "text"}."""
        return {"id": obj.id, "text": self.label(obj)}

    def _base_query(self, **filters):
        query = self.model.query
        if hasattr(self.model, "is_active"):
            query = query.filter_by(is_active=True)
        for key, value in filters.items():
            if value is not None:
                query = query.filter(getattr(self.model, key) == value)
        return query

    def _apply_org_scope(self, query, user):
        """Restrict results to branches/business units the user's scope
        covers — the search/select2/table endpoints share this with
        VehicleService.list()/BaseTransactionService.list() and every
        other org-scope-aware list() this session, so a Manila-scoped
        user can't pick a Cebu vehicle from a dropdown even though the
        Vehicle Master list already hides it. Same rollout-safety rule:
        no scope rows assigned at all, or a GLOBAL/COMPANY scope, means
        unrestricted; records with no branch_id recorded are visible to
        everyone regardless of scope."""
        if user is None or not hasattr(self.model, "branch_id"):
            return query
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scopes = UserOrgScopeService().list_for_user(user.id)
        if not scopes:
            return query
        if any(s.scope_type in ("GLOBAL", "COMPANY") for s in scopes):
            return query
        branch_ids = [s.branch_id for s in scopes if s.scope_type == "BRANCH"]
        conditions = [self.model.branch_id.is_(None)]
        if branch_ids:
            conditions.append(self.model.branch_id.in_(branch_ids))
        if hasattr(self.model, "business_unit_id"):
            bu_ids = [s.business_unit_id for s in scopes
                     if s.scope_type == "BUSINESS_UNIT"]
            if bu_ids:
                conditions.append(self.model.business_unit_id.in_(bu_ids))
        return query.filter(db.or_(*conditions))

    def search(self, q=None, page=1, per_page=None, sort_by=None,
              sort_dir="asc", user=None, **filters):
        """Return (items, total_count) for the given search term/page.
        sort_by is validated against `sortable_fields`; anything else
        (missing, unknown, or unsortable) falls back to id order. Pass
        `user` to apply the same org-scope filtering used everywhere
        else — omit it (or pass None) for internal/unrestricted callers,
        exactly as before."""
        per_page = min(per_page or self.default_per_page, self.max_per_page)
        query = self._base_query(**filters)
        query = self._apply_org_scope(query, user)

        if q:
            like = f"%{q}%"
            conditions = [getattr(self.model, f).ilike(like)
                         for f in self.search_fields]
            query = query.filter(db.or_(*conditions))

        total = query.count()

        if sort_by and sort_by in self.sortable_fields:
            column = getattr(self.model, sort_by)
            order = column.desc() if sort_dir == "desc" else column.asc()
        else:
            order = self.model.id.asc()

        items = (query.order_by(order)
                .offset((page - 1) * per_page)
                .limit(per_page)
                .all())
        return items, total

    def to_select2_response(self, q=None, page=1, per_page=None, user=None,
                            **filters):
        """Return a dict in Select2's AJAX response shape:
        {"results": [{"id", "text"}], "pagination": {"more": bool}}."""
        per_page = min(per_page or self.default_per_page, self.max_per_page)
        items, total = self.search(q=q, page=page, per_page=per_page,
                                   user=user, **filters)
        return {
            "results": [{"id": obj.id, "text": self.label(obj)} for obj in items],
            "pagination": {"more": (page * per_page) < total},
            "total": total,
        }

    def to_table_response(self, q=None, page=1, per_page=None,
                          sort_by=None, sort_dir="asc", user=None,
                          **filters) -> dict:
        """Return the Search Modal's table response shape:
        {"rows": [...], "total": int, "page": int, "per_page": int,
        "total_pages": int}."""
        per_page = min(per_page or self.default_per_page, self.max_per_page)
        items, total = self.search(q=q, page=page, per_page=per_page,
                                   sort_by=sort_by, sort_dir=sort_dir,
                                   user=user, **filters)
        total_pages = max(1, (total + per_page - 1) // per_page)
        return {
            "rows": [self.row(obj) for obj in items],
            "total": total, "page": page, "per_page": per_page,
            "total_pages": total_pages,
        }
