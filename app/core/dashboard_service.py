"""Computes the KPI numbers behind the 6 dashboard widget cards (FLEET,
MAINTENANCE, APPROVALS, REGISTRATIONS, TIRES, BATTERIES). Every count is
org-scope aware — reuses the same list(user=...) filtering already built
into each master-data/transaction service, so a Manila-scoped user's Fleet
count reflects Manila's fleet, not the whole company."""
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.core.approval.task_service import ApprovalTaskService


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

    def fleet_count(self, user=None, branch_id=None) -> int:
        return len(VehicleService().list(include_inactive=False, user=user,
                                         branch_id=branch_id))

    def tire_stock_count(self, user=None, branch_id=None) -> int:
        return len(TireService().list(status="IN_STOCK", user=user,
                                      branch_id=branch_id))

    def battery_stock_count(self, user=None, branch_id=None) -> int:
        return len(BatteryService().list(status="IN_STOCK", user=user,
                                         branch_id=branch_id))

    def maintenance_due_count(self, user=None, branch_id=None) -> int:
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        due = PMDueCalculationService().get_all_due_vehicles()
        if branch_id is not None:
            due = [d for d in due if d["vehicle"].branch_id == branch_id]
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

    def approvals_pending_count(self, user) -> int:
        if user is None:
            return 0
        return len(ApprovalTaskService().list_for_user(user))

    def registrations_expiring_count(self, user=None, days_ahead: int = 30,
                                     branch_id=None) -> int:
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
        if user is None:
            return len(due)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return len([r for r in due
                   if scope_svc.covers(user.id,
                                      branch_id=r["vehicle"].branch_id)])
