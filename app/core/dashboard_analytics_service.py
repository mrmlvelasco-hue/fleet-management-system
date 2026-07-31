"""Dashboard/Analytics chart data.

Every method returns a plain {labels: [...], data: [...]} shape ready
for Chart.js, computed with a single aggregate SQL query per chart --
deliberately not a Python loop over ORM objects, for the same reason the
due-calculation N+1 fixes mattered earlier in this project: this runs
on every dashboard load, and its cost must not scale with fleet size.

Org-scoping (branch visibility) is applied via a WHERE IN on the
branch ids a user can see, resolved once per call rather than per row.
"""
from datetime import date

from sqlalchemy import func, case, extract

from app.extensions import db
from app.core.request_cache import request_cached

# A fixed, deliberately small palette shared by every chart so status
# meanings stay visually consistent across the dashboard and the
# Analytics page (e.g. "ACTIVE" is always the same green everywhere).
COLORS = {
    "ACTIVE": "#2E9CA8", "IN_REPAIR": "#E8A33D", "INACTIVE": "#8592A3",
    "DISPOSED": "#B0392D",
    "OVERDUE": "#C0392B", "DUE": "#E8A33D", "DUE_SOON": "#E8A33D",
    "UPCOMING": "#1C7293", "GOOD": "#2E9CA8",
    "PM": "#065A82", "CORRECTIVE": "#E8A33D", "OPERATIONAL": "#6B7F8F",
    "PALETTE": ["#065A82", "#1C7293", "#2E9CA8", "#E8A33D", "#B0392D",
               "#6B7F8F", "#8592A3", "#3E7CB1"],
}


class DashboardAnalyticsService:

    def _visible_branch_ids(self, user):
        """None means unrestricted (skip the filter entirely, for
        efficiency); otherwise a list of branch ids to filter to.

        UserOrgScopeService has no bulk "list the branches this user can
        see" method -- only a per-branch covers() check, matching how
        org-scoping is already applied elsewhere (the due-vehicle lists
        filter their materialised Python results row by row). For a
        SQL-aggregated chart that isn't practical, so the branch-id set
        is resolved ONCE here from the user's own scope rows, then used
        as a single WHERE branch_id IN (...) -- covers() itself is not
        called per chart row.
        """
        if user is None:
            return None
        from app.modules.user_management.org_scope_service import (
            UserOrgScopeService)
        from app.modules.master_data.org.models import Branch

        scopes = UserOrgScopeService().list_for_user(user.id)
        if not scopes:
            return None  # no scope assigned yet -- unrestricted (matches covers())
        if any(s.scope_type in ("GLOBAL", "COMPANY") for s in scopes):
            return None
        branch_ids = {s.branch_id for s in scopes
                     if s.scope_type == "BRANCH" and s.branch_id is not None}
        if not branch_ids:
            return None  # no recognised scope rows -- fail open, not closed
        return list(branch_ids)

    @request_cached("chart_fleet_by_status")
    def fleet_by_status(self, user=None) -> dict:
        from app.modules.master_data.vehicle.models import Vehicle
        q = (db.session.query(Vehicle.status, func.count(Vehicle.id))
            .filter(Vehicle.is_active.is_(True))
            .group_by(Vehicle.status))
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(branch_ids))
        rows = q.all()
        labels = [r[0] for r in rows]
        return {
            "labels": labels,
            "data": [r[1] for r in rows],
            "colors": [COLORS.get(l, COLORS["PALETTE"][i % 8])
                      for i, l in enumerate(labels)],
        }

    @request_cached("chart_fleet_by_branch")
    def fleet_by_branch(self, user=None) -> dict:
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.master_data.org.models import Branch
        # Active, non-disposed -- matches what "Fleet" means everywhere
        # else on the dashboard (the KPI card excludes disposed too).
        q = (db.session.query(Branch.name, func.count(Vehicle.id))
            .join(Vehicle, Vehicle.branch_id == Branch.id)
            .filter(Vehicle.is_active.is_(True),
                   Vehicle.status != "DISPOSED")
            .group_by(Branch.name)
            .order_by(func.count(Vehicle.id).desc()))
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(branch_ids))
        rows = q.all()
        return {
            "labels": [r[0] for r in rows],
            "data": [r[1] for r in rows],
            "colors": [COLORS["PALETTE"][i % 8] for i in range(len(rows))],
        }

    @request_cached("chart_maintenance_cost_trend")
    def maintenance_cost_trend(self, months: int = 6, user=None) -> dict:
        """Actual cost of COMPLETED maintenance orders, summed per
        calendar month, for the last `months` months including the
        current one."""
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)

        today = date.today()
        month_keys = []
        y, m = today.year, today.month
        for _ in range(months):
            month_keys.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        month_keys.reverse()
        earliest_year, earliest_month = month_keys[0]
        start = date(earliest_year, earliest_month, 1)

        q = (db.session.query(
                extract("year", MaintenanceOrder.completed_date),
                extract("month", MaintenanceOrder.completed_date),
                func.coalesce(func.sum(MaintenanceOrder.actual_cost), 0))
            .filter(MaintenanceOrder.status == "COMPLETED",
                   MaintenanceOrder.completed_date >= start)
            .group_by(
                extract("year", MaintenanceOrder.completed_date),
                extract("month", MaintenanceOrder.completed_date)))
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            from app.modules.master_data.vehicle.models import Vehicle
            q = (q.join(Vehicle, Vehicle.id == MaintenanceOrder.vehicle_id)
                .filter(Vehicle.branch_id.in_(branch_ids)))
        totals = {(int(yr), int(mo)): float(total) for yr, mo, total in q.all()}

        labels = [date(yr, mo, 1).strftime("%b %Y") for yr, mo in month_keys]
        data = [round(totals.get((yr, mo), 0), 2) for yr, mo in month_keys]
        return {"labels": labels, "data": data}

    @request_cached("chart_pm_compliance")
    def pm_compliance(self, user=None) -> dict:
        """Reuses the SAME due-calculation the dashboard's due-list and
        KPI already use, so this chart can never disagree with them --
        the exact bug fixed earlier when the KPI and list used two
        different sources. GOOD is derived as (visible fleet) minus
        (vehicles appearing in the due list), since GOOD vehicles are
        deliberately never materialised by that service."""
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        from app.modules.master_data.vehicle.models import Vehicle

        due = PMDueCalculationService().get_all_due_vehicles(
            exclude_with_open_order=False)
        counts = {"OVERDUE": 0, "DUE_SOON": 0, "UPCOMING": 0}
        flagged_vehicle_ids = set()
        for d in due:
            status = d["status"]
            if status in counts:
                counts[status] += 1
            flagged_vehicle_ids.add(d["vehicle"].id)

        q = db.session.query(func.count(Vehicle.id)).filter(
            Vehicle.is_active.is_(True), Vehicle.status != "DISPOSED")
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(branch_ids))
        total_fleet = q.scalar() or 0
        counts["GOOD"] = max(0, total_fleet - len(flagged_vehicle_ids))

        order = ["OVERDUE", "DUE_SOON", "UPCOMING", "GOOD"]
        display = {"OVERDUE": "Overdue", "DUE_SOON": "Due",
                  "UPCOMING": "Upcoming", "GOOD": "Good"}
        return {
            "labels": [display[k] for k in order],
            "data": [counts[k] for k in order],
            "colors": [COLORS[k] for k in order],
        }

    @request_cached("chart_mo_by_type")
    def maintenance_orders_by_type(self, user=None) -> dict:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        from app.modules.master_data.reference.models import MaintenanceType

        bucket = case(
            (MaintenanceOrder.order_category == "OPERATIONAL", "Operational"),
            (MaintenanceType.category == "PREVENTIVE", "Preventive Maintenance"),
            (MaintenanceType.category == "CORRECTIVE", "Corrective Maintenance"),
            (MaintenanceType.category == "PREDICTIVE", "Predictive Maintenance"),
            else_="Other")

        q = (db.session.query(bucket, func.count(MaintenanceOrder.id))
            .outerjoin(MaintenanceType,
                      MaintenanceType.id == MaintenanceOrder.maintenance_type_id)
            .group_by(bucket))
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            from app.modules.master_data.vehicle.models import Vehicle
            q = (q.join(Vehicle, Vehicle.id == MaintenanceOrder.vehicle_id)
                .filter(Vehicle.branch_id.in_(branch_ids)))
        rows = [r for r in q.all() if r[1] > 0]
        rows.sort(key=lambda r: r[1], reverse=True)
        return {
            "labels": [r[0] for r in rows],
            "data": [r[1] for r in rows],
            "colors": [COLORS["PALETTE"][i % 8] for i in range(len(rows))],
        }

    def all_charts(self, user=None) -> dict:
        return {
            "fleet_by_status": self.fleet_by_status(user=user),
            "fleet_by_branch": self.fleet_by_branch(user=user),
            "maintenance_cost_trend": self.maintenance_cost_trend(user=user),
            "pm_compliance": self.pm_compliance(user=user),
            "mo_by_type": self.maintenance_orders_by_type(user=user),
        }
