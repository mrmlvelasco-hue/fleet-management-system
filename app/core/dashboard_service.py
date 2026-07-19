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
    def fleet_count(self, user=None) -> int:
        return len(VehicleService().list(include_inactive=False, user=user))

    def tire_stock_count(self, user=None) -> int:
        return len(TireService().list(status="IN_STOCK", user=user))

    def battery_stock_count(self, user=None) -> int:
        return len(BatteryService().list(status="IN_STOCK", user=user))

    def maintenance_due_count(self, user=None) -> int:
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        due = PMDueCalculationService().get_all_due_vehicles()
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

    def registrations_expiring_count(self, user=None, days_ahead: int = 30) -> int:
        from app.modules.transactions.vehicle_registration.service import (
            VehicleRegistrationService)
        expiring = VehicleRegistrationService().get_expiring_registrations(
            days_ahead=days_ahead)
        if user is None:
            return len(expiring)
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        scope_svc = UserOrgScopeService()
        return len([r for r in expiring
                   if scope_svc.covers(user.id, branch_id=r["vehicle"].branch_id)])
