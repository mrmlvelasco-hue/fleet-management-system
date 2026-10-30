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


def company_filter(model, company_id):
    """The same rule as a SQL predicate, for `query.filter(...)`.

    Returns a predicate matching records whose EFFECTIVE company is
    `company_id`: either their own, or -- only when they have none of
    their own -- their branch's. Callers that have not joined Branch can
    use this directly; the branch half is expressed as a correlated
    subquery precisely so it composes with any existing query without
    forcing a join the caller did not ask for.

    Passing no company_id yields an always-true predicate, so a caller
    can apply it unconditionally without branching.
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
