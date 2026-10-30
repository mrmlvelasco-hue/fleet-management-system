"""Computes the KPI numbers behind the 6 dashboard widget cards (FLEET,
MAINTENANCE, APPROVALS, REGISTRATIONS, TIRES, BATTERIES). Every count is
org-scope aware — reuses the same list(user=...) filtering already built
into each master-data/transaction service, so a Manila-scoped user's Fleet
count reflects Manila's fleet, not the whole company."""
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.core.approval.task_service import ApprovalTaskService


def _company_of(vehicle, mapped=None):
    """Effective Company of a vehicle: its own, else its branch's.

    The Python-side twin of company_resolution.company_filter, for the
    counts that filter in Python because their source is a calculation
    service rather than a query.
    """
    from app.core.company_resolution import effective_company_id
    return effective_company_id(vehicle)


class DashboardService:
    """Every method takes an optional `branch_id` so a caller can narrow
    to a single branch WITHOUT bypassing org-scoping: `user` is still
    applied, and `branch_id` only ever narrows further. A user who
    cannot see a branch therefore gets an empty result rather than that
    branch's data, even if they pass its id directly. The API layer
    additionally rejects unknown ids outright -- see
    app/modules/api/dashboard.py -- so a mistyped filter surfaces as an
    error rather than as silently company-wide figures.

    Defaults are None throughout, so the Jinja dashboard's existing
    calls are unchanged.
    """

    def fleet_count(self, user=None, branch_id=None, company_id=None) -> int:
        # Stage 3C: company_id narrows by EFFECTIVE Company (the
        # vehicle's own, else its branch's). It composes with branch_id
        # rather than replacing it -- during the Branch->Company
        # transition both filters are offered, and asking for a branch
        # outside the chosen Company must narrow to nothing, not
        # silently drop one of the two.
        return len(VehicleService().list(include_inactive=False, user=user,
                                         branch_id=branch_id,
                                         company_id=company_id))

    def tire_stock_count(self, user=None, branch_id=None,
                         company_id=None) -> int:
        return len(TireService().list(status="IN_STOCK", user=user,
                                      branch_id=branch_id,
                                      company_id=company_id))

    def battery_stock_count(self, user=None, branch_id=None,
                            company_id=None) -> int:
        return len(BatteryService().list(status="IN_STOCK", user=user,
                                         branch_id=branch_id,
                                         company_id=company_id))

    def maintenance_due_count(self, user=None, branch_id=None,
                              company_id=None) -> int:
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        due = PMDueCalculationService().get_all_due_vehicles()
        if branch_id is not None:
            due = [d for d in due if d["vehicle"].branch_id == branch_id]
        if company_id is not None:
            due = [d for d in due
                   if _company_of(d["vehicle"]) == company_id]
        if user is None:
            return len(due)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return len([d for d in due
                   if scope_svc.covers(user.id, branch_id=d["vehicle"].branch_id)])

    def recent_vehicles(self, user=None, limit: int = 10) -> list:
        """Return the most recent org-scoped vehicles for the Vehicle List
        dashboard widget. Reuses VehicleService.list so org-scope filtering
        is identical to the master-data list page."""
        vehicles = VehicleService().list(include_inactive=False, user=user)
        vehicles = sorted(vehicles, key=lambda v: v.id, reverse=True)
        return vehicles[:limit]

    def approvals_pending_count(self, user, branch_id=None,
                                company_id=None) -> int:
        """Tasks awaiting this user's decision, optionally scoped to one
        branch.

        branch_id was previously not accepted at all, while every other
        count on the same dashboard summary already took it -- so the
        approvals tile reported every branch regardless of the header's
        branch selector, and disagreed with the worklist beneath it.
        Same class of count/list disagreement already fixed once for
        registrations; see registrations_expiring_count below.
        """
        if user is None:
            return 0
        tasks = ApprovalTaskService().list_for_user(user)
        if branch_id is not None:
            tasks = [t for t in tasks if t.branch_id == branch_id]
        if company_id is not None:
            # company_id is the snapshot taken at submission (D7); a
            # task raised before that column existed carries None and is
            # correctly excluded from a Company-filtered view.
            tasks = [t for t in tasks
                     if getattr(t, "company_id", None) == company_id]
        return len(tasks)

    def registrations_expiring_count(self, user=None, days_ahead: int = 30,
                                     branch_id=None,
                                     company_id=None) -> int:
        """Count of vehicles needing registration attention.

        Deliberately uses the SAME RegistrationDueCalculationService that
        builds the "Vehicles Due for Registration Renewal" list directly
        below it on the dashboard. It previously called a separate
        get_expiring_registrations(days_ahead=...) lookup, which only
        looked FORWARD -- so an already-expired registration appeared in
        the list but not in the count, and the card read 0 while the list
        underneath it showed 1. One source means the two can no longer
        disagree.
        """
        from app.modules.registration_config.service import (
            RegistrationDueCalculationService)
        due = RegistrationDueCalculationService().get_all_due_vehicles()
        if branch_id is not None:
            due = [d for d in due if d["vehicle"].branch_id == branch_id]
        if company_id is not None:
            due = [d for d in due
                   if _company_of(d["vehicle"]) == company_id]
        if user is None:
            return len(due)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return len([r for r in due
                   if scope_svc.covers(user.id,
                                      branch_id=r["vehicle"].branch_id)])
