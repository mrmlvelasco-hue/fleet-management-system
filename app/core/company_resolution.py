"""Effective-Company resolution (Stage 3C).

ONE definition of "which Company does this record belong to", reused by
every service that filters by Company, so a dashboard count and the list
screen behind it can never disagree about what "belongs to" means.

The rule:

    effective company = the record's own company_id,
                        else its branch's company_id,
                        else None

`Vehicle.company_id` (Stage 3A, D6) is the per-record override and wins
when set. `Branch.company_id` (v277) is the shared default, so mapping a
handful of legacy branches gives every record under them a Company with
no per-record backfill -- which is what keeps the Branch->Company
migration tool off the critical path.

None is a legitimate answer, not an error: a branch nobody has mapped
yet is the normal starting state during master-data setup.
"""
from app.extensions import db
from app.modules.master_data.org.models import Branch


def effective_company_id(record):
    """The Company this record belongs to, or None.

    Works for any record carrying `company_id` and/or `branch_id` --
    Vehicle, Tire, Battery, Driver, Vendor -- so a new branch-scoped
    master needs no column and no change here.
    """
    own = getattr(record, "company_id", None)
    if own is not None:
        return own
    branch = getattr(record, "branch", None)
    return getattr(branch, "company_id", None) if branch is not None else None


def effective_company(record):
    """The OrgCompany object this record belongs to, or None -- the
    same rule as `effective_company_id()`, but returning the object
    itself (via the already-loaded relationships, no extra query) so a
    caller that needs the label, not just the id, doesn't have to
    re-fetch by id. Used wherever a report groups/displays by Company
    rather than only filtering by it (e.g. the Fuel Analysis report's
    per-vehicle identity column)."""
    own = getattr(record, "company", None)
    if own is not None:
        return own
    branch = getattr(record, "branch", None)
    return getattr(branch, "company", None) if branch is not None else None


def company_label(company):
    """One Company rendered for display: short name plus the Region and
    Area/District that make it unique (D2's own uniqueness key) --
    short_name legitimately repeats across Companies (Michael's own
    fleet has five "Excellence" rows), so a bare short_name is not
    enough to tell two apart on a chart legend any more than on a
    picker. Falls back gracefully if either side is somehow missing.

    Moved here from reference.py's `_company_label` (2026-11-02) so the
    Company picker and any report that groups by Company (e.g. the
    dashboard's cost-trend chart) render the same label the same way,
    rather than each defining it again and risking drift.
    """
    if company is None:
        return None
    parts = [p.name for p in (company.region, company.area_district)
            if p is not None]
    return f"{company.short_name} ({' / '.join(parts)})" if parts else company.short_name


def companies_in_scope(scope):
    """Company ids covered by an ORG_REGION / ORG_AREA_DISTRICT
    UserOrgScope. A holder never needs every Company in their Region
    granted individually -- the same resolution UserOrgScopeService.
    covers() performs, expressed here for the SQL/query-filter path so
    Vehicle.list_page() and SearchableService._apply_org_scope() (and
    any future org-scope-aware query) share one definition rather than
    two that could silently drift apart."""
    from app.modules.master_data.org.models import OrgCompany
    q = OrgCompany.query
    if scope.scope_type == "ORG_REGION" and scope.region_id:
        q = q.filter(OrgCompany.region_id == scope.region_id)
    elif (scope.scope_type == "ORG_AREA_DISTRICT"
          and scope.area_district_id):
        q = q.filter(OrgCompany.area_district_id == scope.area_district_id)
    else:
        return []
    return [c.id for c in q.all()]


def _company_key(company):
    """What makes two Company ROWS the same company: short name and
    company name together, ignoring case and outer spaces. Region and
    Area/District are deliberately NOT part of it -- they are what
    distinguishes one company's rows from each other."""
    return ((company.short_name or "").strip().lower(),
            (company.company_name or "").strip().lower())


def company_display(company):
    """One COMPANY rendered for a filter dropdown: "Short Name - Company
    Name", no Region/Area (client decision, 2026-11-16).

    `company_label()` above stays the label of one company ROW (it keeps
    Region / Area) for the places that still pick or print a row: the
    Vehicle form and the Vehicle Register report.

    A company name that is missing, or merely repeats the short name
    ("PERSONAL" / "PERSONAL"), is not shown twice.
    """
    if company is None:
        return None
    short = (company.short_name or "").strip()
    name = (company.company_name or "").strip()
    if not name or name.lower() == short.lower():
        return short
    return f"{short} - {name}"


def company_options(companies):
    """Collapse Company rows into ONE dropdown option per company.

    `companies` is whatever rows the caller is allowed to offer (already
    filtered to active, and to the caller's own company where that
    applies) -- this only groups and labels, it never widens the list.

    Each option's id is the LOWEST row id of that company, so it is
    stable from one request to the next. Any of the company's row ids
    selects the same thing (see `company_filter`), so which one is sent
    never changes the result.
    """
    groups = {}
    for c in companies:
        key = _company_key(c)
        best = groups.get(key)
        if best is None or c.id < best.id:
            groups[key] = c
    options = [{"id": c.id, "short_name": (c.short_name or "").strip(),
                "company_name": c.company_name,
                "label": company_display(c)}
               for c in groups.values()]
    options.sort(key=lambda o: (o["label"].lower(), o["id"]))
    return options


def company_group_subquery(company_id):
    """Ids of every Company row that is the same company as
    `company_id` -- same short name and company name, see _company_key
    -- as a subquery, so a filter costs no extra round trip."""
    from sqlalchemy import func
    from sqlalchemy.orm import aliased
    from app.modules.master_data.org.models import OrgCompany

    sibling = aliased(OrgCompany)
    picked = aliased(OrgCompany)

    def _norm(col):
        return func.lower(func.trim(func.coalesce(col, "")))

    return (db.session.query(sibling.id)
            .join(picked, db.and_(
                _norm(sibling.short_name) == _norm(picked.short_name),
                _norm(sibling.company_name) == _norm(picked.company_name)))
            .filter(picked.id == company_id))


def company_group_ids(company_id):
    """The same set as a Python `set`, for the few filters that run over
    already-loaded rows (the due-list counts) instead of in SQL:

        ids = company_group_ids(company_id)
        rows = [r for r in rows if effective_company_id(r) in ids]

    Includes the company's DEACTIVATED rows on purpose: an area that was
    switched off still holds that company's vehicles, and leaving them
    out would make a company's total disagree with "All companies".
    Empty for no id or an unknown one.
    """
    if not company_id:
        return set()
    # Within one web request the answer cannot change, and a report
    # that checks row by row (reporting.generators._vehicle_matches)
    # would otherwise ask once per vehicle.
    from flask import g, has_request_context
    cache = None
    if has_request_context():
        cache = g.__dict__.setdefault("_company_group_ids", {})
        if company_id in cache:
            return cache[company_id]
    ids = {row[0] for row in company_group_subquery(company_id).all()}
    if cache is not None:
        cache[company_id] = ids
    return ids


def company_filter(model, company_id):
    """A FILTER by company, as a SQL predicate for `query.filter(...)`.

    Matches records whose EFFECTIVE company -- their own, or, only when
    they have none of their own, their branch's -- is `company_id` OR
    ANY OTHER ROW OF THE SAME COMPANY.

    2026-11-16: the dashboard, mobile app and list filters now show each
    company once ("Short Name - Company Name"), so choosing "Excellence"
    has to mean all of Excellence, not the one Region/Area row whose id
    happens to be behind the option. Any of the company's row ids gives
    the same result.

    This is for NARROWING what the caller already may see. An access
    boundary -- a user's ORG_COMPANY scope, a broadcast's audience --
    must use `exact_company_filter` instead, which stays one row.

    The branch half is a correlated subquery precisely so it composes
    with any existing query without forcing a join the caller did not
    ask for. Passing no company_id yields an always-true predicate, so a
    caller can apply it unconditionally without branching.
    """
    if not company_id:
        return db.true()
    ids = company_group_subquery(company_id)
    has_own = model.company_id.isnot(None)
    via_branch = model.branch_id.in_(
        db.session.query(Branch.id).filter(Branch.company_id.in_(ids))
    )
    return db.or_(
        db.and_(has_own, model.company_id.in_(ids)),
        db.and_(~has_own, via_branch),
    )


def exact_company_filter(model, company_id):
    """The effective-company rule for exactly ONE Company row.

    For access boundaries: a user granted "Excellence / CAVITE 2" sees
    that row's records and no other Excellence area's. Showing a company
    once in a dropdown must never widen what anybody is allowed to see,
    so every org-scope call site uses this, never `company_filter`.
    """
    if not company_id:
        return db.true()
    has_own = model.company_id.isnot(None)
    via_branch = model.branch_id.in_(
        db.session.query(Branch.id).filter(Branch.company_id == company_id)
    )
    return db.or_(
        db.and_(has_own, model.company_id == company_id),
        db.and_(~has_own, via_branch),
    )
