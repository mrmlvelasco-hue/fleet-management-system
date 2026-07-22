"""PM Package Recommendation Service.

Sits on top of PMDueCalculationService and answers a more specific
question the Maintenance Order form needs: given a vehicle's PM profile
(an ORDERED cycle of packages sharing one profile_code -- e.g. a "first
1,000 km" package, then a package recurring every 5,000 km), WHICH
package comes next, is it Upcoming / Due / Overdue, and why -- so the MO
form can auto-select it in the PM Scope dropdown.

Model (confirmed against the real PM_Task_List data and the user's
spec):

- A PMS Profile is the set of PMSchedule rows sharing a profile_code,
  ordered by sequence_position. Each row's interval_km/interval_days is
  its RECURRING step (do this package every N km / N days since the last
  service), NOT a cumulative odometer milestone.

- "Next package" = the one after the last COMPLETED package in
  sequence_position order (wrapping: after the last special first-
  service package, the cycle settles onto the recurring package). If no
  package has ever been completed, the next package is the first in the
  sequence.

- Whichever-comes-first: next due odometer = last service odometer +
  next package's interval_km; next due date = last service date + next
  package's interval_days. DUE if the vehicle has reached EITHER;
  OVERDUE past either; UPCOMING within the notify window; else GOOD.

- Deliberately does NOT dump a whole backlog of long-passed milestones
  as overdue for a high-odometer vehicle with no logged history (per the
  user's explicit choice): it recommends the single NEXT package and
  flags only that one against the upcoming window, rather than every
  interval the odometer has ever swept past.
"""
from datetime import date, timedelta

from app.extensions import db
from app.modules.maintenance_config.models import PMSchedule
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.core.maintenance.due_calculation_service import (
    PMDueCalculationService)


class PMPackageRecommendationService:
    def __init__(self):
        self._due_svc = PMDueCalculationService()

    def _profile_packages(self, schedule: PMSchedule) -> list:
        """All packages in the same PMS Profile cycle as `schedule`,
        ordered by sequence_position. A schedule with no profile_code is
        a standalone single-package schedule -- its own one-item cycle."""
        if not schedule.profile_code:
            return [schedule]
        # NOTE: sort in PYTHON, not via SQL ORDER BY ... NULLS LAST.
        # `.nullslast()` emits the `NULLS LAST` keyword, which PostgreSQL
        # and SQLite accept but MySQL rejects outright with a 1064 syntax
        # error (this exact crash happened on the production MySQL
        # database while passing locally on SQLite). This matches the
        # already-established portable pattern in
        # PMScheduleService.get_profile() for the very same reason -- a
        # profile's package count is always small, so sorting after
        # fetching is cheap and fully dialect-independent. NULL
        # sequence_position sorts last.
        rows = (PMSchedule.query
               .filter_by(profile_code=schedule.profile_code, is_active=True)
               .all())
        return sorted(rows, key=lambda r: (
            r.sequence_position is None, r.sequence_position or 0))

    def _last_completed_package(self, vehicle_id: int, packages: list):
        """The most recently COMPLETED MO whose pm_schedule_id is one of
        this profile's packages -- returns (package, order) or
        (None, None)."""
        pkg_ids = [p.id for p in packages]
        order = (MaintenanceOrder.query
                .filter(MaintenanceOrder.vehicle_id == vehicle_id,
                       MaintenanceOrder.pm_schedule_id.in_(pkg_ids),
                       MaintenanceOrder.status == "COMPLETED")
                .order_by(MaintenanceOrder.completed_date.desc())
                .first())
        if order is None:
            return None, None
        pkg = next((p for p in packages if p.id == order.pm_schedule_id), None)
        return pkg, order

    def _next_package(self, packages: list, last_pkg, current_km=0) -> "PMSchedule":
        """The package after last_pkg in sequence order. Special-cases
        the no-history vehicle: package[0] is typically the one-time
        "first N km" service (e.g. first 1,000 km), which only makes
        sense for a genuinely new vehicle. A no-history vehicle that's
        ALREADY well past that first-service interval (e.g. an existing
        60,000 km fleet vehicle being entered into the system for the
        first time) should settle straight onto the RECURRING steady-
        state package, not be told its "first 1,000 km" service is next.
        """
        if last_pkg is None:
            first = packages[0]
            # If the vehicle is still within its first-service interval,
            # the first-service package is correct. Otherwise jump to the
            # recurring package (the last row of the pre-expanded cycle).
            if (first.interval_km and current_km
                    and current_km > first.interval_km):
                return packages[-1]
            return first
        idx = next((i for i, p in enumerate(packages) if p.id == last_pkg.id),
                  None)
        if idx is None or idx + 1 >= len(packages):
            # Completed the last defined package -> the recurring one
            # (the final row in the pre-expanded cycle) repeats.
            return packages[-1]
        return packages[idx + 1]

    def _select_by_milestone(self, packages, current_km, last_order):
        """When packages carry cumulative_km milestones (5,000 / 10,000 /
        ... / 65,000 / 105,000), pick the package the vehicle actually
        needs by ODOMETER -- the first milestone strictly greater than
        where the vehicle has already been serviced (or, with no history,
        the first milestone at/above the current odometer). This is what
        makes a 65,000 km vehicle land on the 65,000-km package (seq 14)
        instead of the last package in the list (seq 22 = 105,000 km).

        Returns (package, is_beyond_last): is_beyond_last is True when the
        vehicle's odometer is past EVERY defined milestone -- in which
        case the LAST package is returned as a sensible default and the
        fleet manager decides which package to actually apply (the scope
        can't be auto-matched beyond the end of the defined cycle).

        Returns (None, False) if no package has a cumulative_km at all,
        signalling the caller to fall back to interval-step math.
        """
        milestoned = [p for p in packages if p.cumulative_km is not None]
        if not milestoned:
            return None, False
        milestoned.sort(key=lambda p: p.cumulative_km)

        if last_order is not None and last_order.odometer_at_service is not None:
            # WITH history: the next package is the first milestone
            # strictly above what was last serviced. If the vehicle has
            # since driven past that milestone without servicing, that
            # missed milestone is what's due (overdue) -- we don't skip
            # ahead. This keeps a missed service visible.
            floor = last_order.odometer_at_service
            candidate = next((p for p in milestoned
                            if p.cumulative_km > floor), None)
        else:
            # NO history: measure FORWARD from where the vehicle is now
            # (don't dump a backlog of every past milestone). The next
            # package is the first milestone AT OR ABOVE the current
            # odometer -- so a 65,000 km vehicle lands on the 65,000-km
            # package, a 62,000 km vehicle on the 65,000-km package, etc.
            candidate = next((p for p in milestoned
                            if p.cumulative_km >= current_km), None)

        if candidate is None:
            # Past every defined milestone -> return the last package as
            # a default; the fleet manager decides which to actually
            # apply (scope can't be auto-matched beyond the defined
            # cycle).
            return milestoned[-1], True
        return candidate, False

    def recommend(self, vehicle, maintenance_type_id=None,
                  as_of_date=None) -> dict:
        """Structured recommendation for the vehicle's next PM package.

        Returns:
          recommended_package (PMSchedule | None)
          due_by ("KM" | "MONTHS" | "BOTH" | None)
          due_odometer (int | None)
          due_date (date | None)
          status ("UPCOMING" | "DUE" | "OVERDUE" | "GOOD")
          reason (str)
          beyond_defined_cycle (bool)  # fleet manager should choose
        """
        as_of_date = as_of_date or date.today()
        schedules = self._due_svc._applicable_schedules(
            vehicle, maintenance_type_id)
        if not schedules:
            return self._empty("No PM schedule applies to this vehicle.")

        packages = self._profile_packages(schedules[0])
        if not packages:
            return self._empty("No PM packages configured for this profile.")

        last_pkg, last_order = self._last_completed_package(
            vehicle.id, packages)
        current_km = vehicle.current_odometer or 0

        # Prefer milestone-based selection (cumulative_km) -- this is the
        # correct, unambiguous way to pick the package for a vehicle at a
        # given odometer. Fall back to sequence-based selection only when
        # the profile has no cumulative_km data at all (e.g. older
        # imports before this field, or purely calendar-based profiles).
        beyond_defined_cycle = False
        milestone_pkg, beyond = self._select_by_milestone(
            packages, current_km, last_order)
        if milestone_pkg is not None:
            next_pkg = milestone_pkg
            beyond_defined_cycle = beyond
        else:
            next_pkg = self._next_package(
                packages, last_pkg, current_km=current_km)

        # Baseline: last completed service's odometer/date, or the
        # vehicle's captured legacy baseline, or (last resort) its
        # current odometer / today -- the latter is what implements
        # "don't dump the whole backlog": an unknown-history vehicle is
        # measured forward from where it is NOW, not from zero.
        if last_order is not None:
            base_km = (last_order.odometer_at_service
                      if last_order.odometer_at_service is not None
                      else (vehicle.current_odometer or 0))
            base_date = last_order.completed_date or as_of_date
        elif (vehicle.last_pm_odometer is not None
              or vehicle.last_pm_date is not None):
            base_km = vehicle.last_pm_odometer or (vehicle.current_odometer or 0)
            base_date = vehicle.last_pm_date or as_of_date
        else:
            base_km = vehicle.current_odometer or 0
            base_date = as_of_date

        due_odometer = None
        due_date = None
        km_due = km_overdue = date_due = date_overdue = False

        notify_km, notify_days = self._notify_windows(next_pkg)

        if next_pkg.trigger_mode in ("KM", "HYBRID"):
            if next_pkg.cumulative_km is not None:
                # The package's own absolute milestone IS the due
                # odometer -- no interval arithmetic needed. This is the
                # correct, unambiguous value (65,000 for the 65,000-km
                # package), and it's why a vehicle at 65,000 km lands on
                # the right package instead of the last one in the list.
                due_odometer = next_pkg.cumulative_km
            elif next_pkg.interval_km:
                # Fallback for profiles with no cumulative_km data: the
                # next interval-multiple strictly above the anchor.
                interval = next_pkg.interval_km
                if last_order is not None or vehicle.last_pm_odometer is not None:
                    anchor = base_km
                else:
                    anchor = current_km
                due_odometer = ((anchor // interval) + 1) * interval

            if due_odometer is not None:
                # At/within the notify window before the milestone = DUE
                # (do it now, on time). Strictly PAST the milestone =
                # OVERDUE (a missed service). Being exactly AT the
                # milestone is on-time, so it's DUE, not overdue.
                if current_km > due_odometer:
                    km_overdue = True
                elif current_km >= due_odometer - notify_km:
                    km_due = True

        if next_pkg.trigger_mode in ("CALENDAR", "HYBRID") and next_pkg.interval_days:
            due_date = base_date + timedelta(days=next_pkg.interval_days)
            days_remaining = (due_date - as_of_date).days
            if days_remaining <= 0:
                date_overdue = True
            elif days_remaining <= notify_days:
                date_due = True

        # Whichever-comes-first: the WORST of the two conditions wins.
        if km_overdue or date_overdue:
            status = "OVERDUE"
        elif km_due or date_due:
            status = "DUE"
        elif due_odometer is not None or due_date is not None:
            status = "UPCOMING"
        else:
            status = "GOOD"

        due_by = self._due_by(next_pkg)
        reason = self._build_reason(
            status, due_by, current_km, due_odometer, due_date, as_of_date,
            km_overdue, km_due, date_overdue, date_due, last_pkg)
        if beyond_defined_cycle:
            reason = ("Vehicle is beyond the last defined PM package in this "
                     "profile — showing the final package; the fleet manager "
                     "should confirm which package to apply. " + reason)

        return {
            "recommended_package": next_pkg,
            "due_by": due_by,
            "due_odometer": due_odometer,
            "due_date": due_date,
            "status": status,
            "reason": reason,
            "beyond_defined_cycle": beyond_defined_cycle,
        }

    def _notify_windows(self, pkg):
        """Reuse the same capped due-soon window logic the base engine
        applies, so 'UPCOMING' here means the same thing as 'DUE_SOON'
        there."""
        svc = self._due_svc
        if pkg.notify_before_days:
            notify_days = pkg.notify_before_days
        elif pkg.interval_days:
            notify_days = min(svc.default_due_soon_days,
                             max(1, pkg.interval_days // 3))
        else:
            notify_days = svc.default_due_soon_days

        if pkg.notify_before_km:
            notify_km = pkg.notify_before_km
        elif pkg.interval_km:
            notify_km = min(svc.default_due_soon_km,
                           max(1, pkg.interval_km // 3))
        else:
            notify_km = svc.default_due_soon_km
        return notify_km, notify_days

    def _due_by(self, pkg) -> str:
        if pkg.trigger_mode == "HYBRID" and pkg.interval_km and pkg.interval_days:
            return "BOTH"
        if pkg.trigger_mode == "KM" and pkg.interval_km:
            return "KM"
        if pkg.trigger_mode == "CALENDAR" and pkg.interval_days:
            return "MONTHS"
        if pkg.interval_km:
            return "KM"
        if pkg.interval_days:
            return "MONTHS"
        return None

    def _build_reason(self, status, due_by, current_km, due_odometer,
                      due_date, as_of_date, km_overdue, km_due,
                      date_overdue, date_date, last_pkg) -> str:
        if status == "GOOD":
            return "No upcoming PM within the current window."
        parts = []
        if km_overdue:
            parts.append(f"odometer {current_km:,} km has passed the due "
                        f"{due_odometer:,} km")
        elif km_due:
            parts.append(f"odometer {current_km:,} km is approaching the due "
                        f"{due_odometer:,} km")
        if date_overdue:
            parts.append(f"due date {due_date} has passed")
        elif date_date:
            parts.append(f"due date {due_date} is approaching")
        if not parts:
            if due_odometer and due_date:
                return (f"next service at {due_odometer:,} km or {due_date}, "
                       f"whichever comes first")
            if due_odometer:
                return f"next service at {due_odometer:,} km"
            if due_date:
                return f"next service on {due_date}"
        return "; ".join(parts).capitalize()

    def _empty(self, reason: str) -> dict:
        return {"recommended_package": None, "due_by": None,
               "due_odometer": None, "due_date": None,
               "status": "GOOD", "reason": reason,
               "beyond_defined_cycle": False}
