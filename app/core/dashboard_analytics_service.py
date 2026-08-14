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
        """Orders split by maintenance discipline, per the client's
        reporting specification.

        Classification comes from TransactionType.maintenance_class --
        DATA, not a hardcoded CASE. The requirement arrived as a 40-code
        SQL CASE, but 25 of those codes are just "every OPERATIONAL
        type", which order_category already answers, and putting the
        rest in a query would mean a newly added transaction type
        silently falls into Unclassified until someone edits SQL. As a
        column it is visible and editable in MO Transaction Types.

        Scope is limited by the DASHBOARD_MO_CHART_YEARS system
        parameter (default 1 = current year only), so this chart doesn't
        get slower and less meaningful every year the system runs. Set
        it to 0 for the whole history.

        CANCELLED orders are excluded -- they represent work that never
        happened and would overstate every bucket.
        """
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder, TransactionType, MAINTENANCE_CLASS_LABELS)
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)

        label_of = MAINTENANCE_CLASS_LABELS

        bucket = case(
            # An OPERATIONAL order is operational regardless of anything
            # else -- this single test replaces 25 hardcoded codes.
            (MaintenanceOrder.order_category == "OPERATIONAL", "OPERATIONAL"),
            (TransactionType.maintenance_class.isnot(None),
             TransactionType.maintenance_class),
            # No transaction type recorded (the older PM orders, which
            # predate transaction types) is preventive by definition.
            (MaintenanceOrder.transaction_type_id.is_(None), "PREVENTIVE"),
            else_="UNCLASSIFIED")

        q = (db.session.query(bucket, func.count(MaintenanceOrder.id))
            .outerjoin(TransactionType,
                      TransactionType.id == MaintenanceOrder.transaction_type_id)
            .filter(MaintenanceOrder.status != "CANCELLED")
            .group_by(bucket))

        years = SystemParameterService().get("DASHBOARD_MO_CHART_YEARS", 1)
        try:
            years = int(years)
        except (TypeError, ValueError):
            years = 1
        if years > 0:
            first_year = date.today().year - (years - 1)
            q = q.filter(
                extract("year", MaintenanceOrder.scheduled_date) >= first_year)

        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            from app.modules.master_data.vehicle.models import Vehicle
            q = (q.join(Vehicle, Vehicle.id == MaintenanceOrder.vehicle_id)
                .filter(Vehicle.branch_id.in_(branch_ids)))

        rows = [r for r in q.all() if r[1] > 0]
        rows.sort(key=lambda r: r[1], reverse=True)
        return {
            "labels": [label_of.get(r[0], "Unclassified") for r in rows],
            "data": [r[1] for r in rows],
            "colors": [COLORS["PALETTE"][i % 8] for i in range(len(rows))],
            "years": years,
        }

    # Each chart's data, keyed by the name the front end asks for.
    # Values are the bound method, not the result -- so a caller wanting
    # one chart pays for one chart.
    def _chart_builders(self, user=None):
        return {
            "fleet_by_status": lambda: self.fleet_by_status(user=user),
            "fleet_by_branch": lambda: self.fleet_by_branch(user=user),
            "maintenance_cost_trend": lambda: self.maintenance_cost_trend(user=user),
            "pm_compliance": lambda: self.pm_compliance(user=user),
            "mo_by_type": lambda: self.maintenance_orders_by_type(user=user),
            "registration_status": lambda: self.registration_status(user=user),
        }

    def all_charts(self, user=None, only=None) -> dict:
        """Chart payloads. `only` limits it to the named charts.

        Why this matters: these six have wildly different costs.
        registration_status and mo_by_type are simple aggregates
        (milliseconds); pm_compliance runs the full fleet-wide PM due
        calculation, evaluating every vehicle against every applicable
        schedule.

        Returned as one combined payload, the cheap charts could not
        render until the most expensive one had finished -- so
        "Vehicle Registration Status" and "Maintenance Management",
        which are individually among the fastest, appeared to take as
        long as the slowest thing on the page. Letting the front end
        request charts individually means each one appears as soon as
        its OWN data is ready.
        """
        builders = self._chart_builders(user=user)
        if only:
            wanted = [k for k in only if k in builders]
        else:
            wanted = list(builders)
        return {k: builders[k]() for k in wanted}

    @request_cached("chart_approval_workflow")
    def approval_workflow_counts(self, user=None) -> dict:
        """Live counts for the Approval Workflow timeline.

        Real counts from ApprovalInstance / MaintenanceOrder status, not
        illustrative numbers -- this sits on an executive dashboard where
        a wrong figure would be read as fact.
        """
        from app.core.approval.models import ApprovalInstance
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)

        def _count(model, **filters):
            return db.session.query(func.count(model.id)).filter_by(
                **filters).scalar() or 0

        return {
            "draft": _count(MaintenanceOrder, status="DRAFT"),
            "submitted": _count(ApprovalInstance, status="PENDING"),
            "for_approval": _count(ApprovalInstance, status="PENDING"),
            "approved": _count(ApprovalInstance, status="APPROVED"),
            "completed": _count(MaintenanceOrder, status="COMPLETED"),
        }

    @request_cached("kpi_trends")
    def kpi_trends(self, user=None) -> dict:
        """Month-over-month change for the KPI cards.

        Computed from each model's created_at against the same day last
        month. Where a meaningful comparison cannot be made (no history
        yet, or a prior count of zero, which would make any percentage
        meaningless) the key is simply ABSENT and the card renders
        without a trend row -- deliberately, rather than showing a
        placeholder or a fabricated figure that a client would read as
        real.
        """
        from datetime import date, timedelta
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)

        cutoff = date.today() - timedelta(days=30)
        trends = {}

        def _trend(key, model, **filters):
            q = db.session.query(func.count(model.id))
            if filters:
                q = q.filter_by(**filters)
            now = q.scalar() or 0
            before = (q.filter(model.created_at < cutoff).scalar() or 0)
            delta = now - before
            if before > 0:
                change = delta / before * 100
                trends[key] = {
                    "pct": round(abs(change), 1),
                    "dir": "up" if change > 0 else
                           ("down" if change < 0 else "flat")}
            elif delta > 0:
                # No baseline: a month ago there were none of these, so a
                # percentage is undefined -- 0 to 156 is not "+100%", and
                # printing one would be inventing a statistic. This is
                # the normal state right after a migration, when the
                # whole fleet was imported on a single day.
                #
                # The ABSOLUTE change is real and measurable, so show
                # that instead of leaving the tile bare.
                trends[key] = {"delta": delta, "dir": "up"}

        def _spark(key, model, **filters):
            """A 6-point monthly series of the running total, for the
            card's sparkline. Real cumulative counts from created_at --
            the same source as the percentage above it, so the line and
            the number can never tell different stories.

            Omitted entirely (like the trend) when there is no history
            to draw: a flat line invented from a single data point would
            imply stability that hasn't been measured."""
            series, base = [], db.session.query(func.count(model.id))
            if filters:
                base = base.filter_by(**filters)
            for months_ago in range(5, -1, -1):
                edge = date.today() - timedelta(days=30 * months_ago)
                series.append(base.filter(model.created_at < edge).scalar() or 0)
            series[-1] = base.scalar() or 0   # today, not 'before today'
            if len(set(series)) <= 1:
                return                        # nothing to show
            trends.setdefault(key, {})["spark"] = series

        _trend("FLEET", Vehicle, is_active=True)
        _trend("MAINTENANCE", MaintenanceOrder)
        _spark("FLEET", Vehicle, is_active=True)
        _spark("MAINTENANCE", MaintenanceOrder)
        return trends

    @request_cached("chart_registration_status")
    def registration_status(self, user=None) -> dict:
        """Registration health of the whole fleet, as the mockup's
        'Vehicle Registration Status' donut: Active / Expiring Soon /
        For Renewal, with counts and percentages.

        Replaces an unbounded table on the dashboard. With ~100 vehicles
        overdue after a migration, that table pushed everything below it
        off the screen; a fleet manager needs the SHAPE here and the
        detail on the report.

        Uses the SAME RegistrationDueCalculationService the due list and
        the Registrations KPI already use, so this panel cannot disagree
        with either.
        """
        from app.modules.registration_config.service import (
            RegistrationDueCalculationService)
        from app.modules.master_data.vehicle.models import Vehicle

        due = RegistrationDueCalculationService().get_all_due_vehicles(
            exclude_with_open_order=False)
        overdue = sum(1 for d in due if d["status"] == "OVERDUE")
        soon = sum(1 for d in due if d["status"] == "DUE_SOON")
        flagged = {d["vehicle"].id for d in due}

        q = db.session.query(func.count(Vehicle.id)).filter(
            Vehicle.is_active.is_(True), Vehicle.status != "DISPOSED")
        branch_ids = self._visible_branch_ids(user)
        if branch_ids is not None:
            q = q.filter(Vehicle.branch_id.in_(branch_ids))
        total = q.scalar() or 0
        active = max(0, total - len(flagged))

        rows = [("Active", active, COLORS["ACTIVE"]),
               ("Expiring Soon", soon, COLORS["DUE"]),
               ("For Renewal", overdue, COLORS["OVERDUE"])]
        return {
            "labels": [r[0] for r in rows],
            "data": [r[1] for r in rows],
            "colors": [r[2] for r in rows],
            "total": total,
            # Percentages computed server-side so the donut, the legend
            # and any export all quote the same rounded figure.
            "pct": [round(r[1] / total * 100, 1) if total else 0.0
                   for r in rows],
        }
