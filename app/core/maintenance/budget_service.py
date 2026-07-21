"""Vehicle maintenance budget tracking.

The CAR_PLAN_BUDGET_Y1..Y5 and COMPANY_OWNED_BUDGET_Y1..Y5 System
Parameters have existed since Phase 1c but were never actually consumed
anywhere — this is what wires them up.

Two tracking modes, both supported (System Parameter BUDGET_TRACKING_MODE,
default PER_YEAR):

  PER_YEAR — each vehicle-year gets its own budget from the matching Y-tier,
  and only that year's spending counts against it. A vehicle that
  underspent in Y1 does NOT carry that headroom into Y2. This matches how
  the Y1..Y5 tiers are structured (rising amounts as a vehicle ages), and
  is the more typical way fleet maintenance budgets are actually tracked —
  it answers "is this vehicle running over its normal cost for a vehicle
  of its age RIGHT NOW", not "has it collectively spent less than its
  lifetime allotment", which can hide a genuinely expensive current year
  behind an underspent earlier one.

  ACCUMULATED — the Y1..Y(current) tiers are summed into one lifetime
  budget pool, compared against total spend since acquisition. Unspent
  budget from an earlier year carries forward. Better suited if the
  5-year Car Plan allotment is meant to be treated as one lump sum the
  vehicle can draw against at any point, rather than 5 separate annual
  ceilings.

Recommendation: PER_YEAR as the default (matches the tiered structure's
intent and gives a cleaner "is this vehicle over its normal budget this
year" signal), with ACCUMULATED available for organizations that
genuinely want a rolling multi-year pool instead. Both are implemented;
switch anytime via System Parameters without any code change.
"""
from datetime import date
from decimal import Decimal


def _age_year(vehicle, as_of_date) -> int:
    """1-indexed vehicle-service-year (Y1 = first year owned), capped at
    5 since no tier beyond Y5 is defined -- a 7-year-old vehicle still
    uses the Y5 rate, on the assumption costs plateau rather than keep
    rising indefinitely."""
    anchor = vehicle.delivery_date or vehicle.acquisition_date
    if anchor is None:
        return None
    years_elapsed = (as_of_date.year - anchor.year) - (
        1 if (as_of_date.month, as_of_date.day) < (anchor.month, anchor.day)
        else 0)
    years_elapsed = max(0, years_elapsed)
    return min(years_elapsed + 1, 5)


def _tier_budget(classification: str, year: int, params) -> Decimal:
    code = f"{classification}_BUDGET_Y{year}"
    value = params.get(code, default=None)
    return Decimal(str(value)) if value is not None else Decimal("0")


class VehicleBudgetService:
    def get_budget_status(self, vehicle, as_of_date=None) -> dict:
        """None fields throughout mean "budget tracking doesn't apply to
        this vehicle" — either it has no assignment_group_classification
        set, that classification isn't CAR_PLAN/COMPANY_OWNED (e.g.
        OTHERS), or it has no acquisition/delivery date to compute an
        age-year from. Callers should treat status=None as "not
        applicable", not as an error or as OVER_BUDGET."""
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        as_of_date = as_of_date or date.today()
        params = SystemParameterService()
        mode = params.get("BUDGET_TRACKING_MODE", default="PER_YEAR")

        classification = vehicle.assignment_group_classification
        if classification not in ("CAR_PLAN", "COMPANY_OWNED"):
            return {"applicable": False, "mode": mode}

        current_year = _age_year(vehicle, as_of_date)
        if current_year is None:
            return {"applicable": False, "mode": mode}

        anchor = vehicle.delivery_date or vehicle.acquisition_date

        if mode == "ACCUMULATED":
            budget = sum(_tier_budget(classification, y, params)
                        for y in range(1, current_year + 1))
            period_start = anchor
            period_end = as_of_date
        else:  # PER_YEAR
            budget = _tier_budget(classification, current_year, params)
            # This vehicle-year's own 12-month window, anchored to its
            # delivery/acquisition date -- NOT the calendar year, since
            # two vehicles delivered on different dates shouldn't share
            # a Jan-Dec budget window.
            from dateutil.relativedelta import relativedelta
            period_start = anchor + relativedelta(years=current_year - 1)
            period_end = anchor + relativedelta(years=current_year)

        spent = self._spent_in_period(vehicle.id, period_start, period_end)
        remaining = budget - spent

        return {
            "applicable": True, "mode": mode,
            "classification": classification, "current_year": current_year,
            "period_start": period_start, "period_end": period_end,
            "budget": budget, "spent": spent, "remaining": remaining,
            "over_budget": remaining < 0,
        }

    def _spent_in_period(self, vehicle_id, period_start, period_end) -> Decimal:
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        q = (MaintenanceOrder.query
            .filter_by(vehicle_id=vehicle_id, status="COMPLETED")
            .filter(MaintenanceOrder.completed_date.isnot(None))
            .filter(MaintenanceOrder.completed_date >= period_start)
            .filter(MaintenanceOrder.completed_date <= period_end))
        return sum((Decimal(str(o.actual_cost)) for o in q.all()
                   if o.actual_cost is not None), Decimal("0"))
