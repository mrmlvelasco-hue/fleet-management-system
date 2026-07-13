"""PM Due-Calculation Service.

Determines whether a vehicle is GOOD / DUE_SOON / OVERDUE for a given
(or the first applicable) PM schedule, using KM, CALENDAR, or HYBRID
("whichever comes first") triggers — the standard manufacturer PM practice.

"Last service" is the most recent COMPLETED MaintenanceOrder for that
vehicle + maintenance type; if none exists yet, the vehicle's own odometer
baseline (0) and creation date are used so a brand-new vehicle isn't
flagged as overdue on day one.

Due-soon thresholds (within N km / within N days) are read from
SystemParameters (PM_DUE_SOON_KM / PM_DUE_SOON_DAYS) with sensible
defaults, so they're tunable without code changes.
"""
from datetime import date

from app.extensions import db
from app.modules.maintenance_config.models import PMSchedule
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)

DEFAULT_DUE_SOON_KM = 500
DEFAULT_DUE_SOON_DAYS = 30

_STATUS_RANK = {"GOOD": 0, "DUE_SOON": 1, "OVERDUE": 2}


def _worse(a: str, b: str) -> str:
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


class PMDueCalculationService:
    def __init__(self):
        params = SystemParameterService()
        self.default_due_soon_km = params.get("PM_DUE_SOON_KM",
                                              default=DEFAULT_DUE_SOON_KM)
        self.default_due_soon_days = params.get("PM_DUE_SOON_DAYS",
                                                default=DEFAULT_DUE_SOON_DAYS)
        try:
            self.default_due_soon_km = int(self.default_due_soon_km)
            self.default_due_soon_days = int(self.default_due_soon_days)
        except (TypeError, ValueError):
            self.default_due_soon_km = DEFAULT_DUE_SOON_KM
            self.default_due_soon_days = DEFAULT_DUE_SOON_DAYS

    def _resolve_vehicle_brand_model_ids(self, vehicle):
        """Resolve a Vehicle's free-text brand/model strings to the real
        VehicleBrand/VehicleModel master IDs, for FK-based PM Template
        matching (PMS-1). Returns (brand_id, model_id), either possibly
        None if no matching master record exists."""
        from app.modules.master_data.vehicle_brand.models import (
            VehicleBrand, VehicleModel)
        brand = VehicleBrand.query.filter(
            db.func.lower(VehicleBrand.name) == (vehicle.brand or "").strip().lower()
        ).first()
        if not brand:
            return None, None
        model = VehicleModel.query.filter(
            VehicleModel.brand_id == brand.id,
            db.func.lower(VehicleModel.name) == (vehicle.model or "").strip().lower()
        ).first()
        return brand.id, (model.id if model else None)

    def _applicable_schedules(self, vehicle: Vehicle, maintenance_type_id=None):
        """Matching precedence (most to least specific):
        1. Vehicle's directly assigned PM template (pm_schedule_id)
        2. Real FK Brand+Model match (vehicle_brand_id/vehicle_model_id)
        3. Exact vehicle_make + vehicle_model free-text match (case-insensitive)
        4. vehicle_type_id match
        5. Global schedule (vehicle_type_id AND make/model all NULL)
        """
        if vehicle.pm_schedule_id:
            sched = db.session.get(PMSchedule, vehicle.pm_schedule_id)
            if sched and sched.is_active and (
                    not maintenance_type_id or
                    sched.maintenance_type_id == maintenance_type_id):
                return [sched]

        base_query = PMSchedule.query.filter_by(is_active=True)
        if maintenance_type_id:
            base_query = base_query.filter_by(
                maintenance_type_id=maintenance_type_id)

        brand_id, model_id = self._resolve_vehicle_brand_model_ids(vehicle)
        if brand_id and model_id:
            fk_matches = base_query.filter_by(
                vehicle_brand_id=brand_id, vehicle_model_id=model_id).all()
            if fk_matches:
                return fk_matches

        make = (vehicle.brand or "").strip().lower()
        model = (vehicle.model or "").strip().lower()
        make_model_matches = [
            s for s in base_query.all()
            if s.vehicle_make and s.vehicle_model
            and s.vehicle_make.strip().lower() == make
            and s.vehicle_model.strip().lower() == model]
        if make_model_matches:
            return make_model_matches

        type_matches = base_query.filter_by(
            vehicle_type_id=vehicle.vehicle_type_id).all()
        type_matches = [s for s in type_matches
                       if not s.vehicle_make and not s.vehicle_model]
        if type_matches:
            return type_matches

        global_matches = base_query.filter_by(vehicle_type_id=None).all()
        return [s for s in global_matches
               if not s.vehicle_make and not s.vehicle_model]

    def _last_service(self, vehicle_id: int, maintenance_type_id: int):
        order = (MaintenanceOrder.query
                 .filter_by(vehicle_id=vehicle_id,
                           maintenance_type_id=maintenance_type_id,
                           status="COMPLETED")
                 .order_by(MaintenanceOrder.completed_date.desc())
                 .first())
        if order:
            return order.odometer_at_service or 0, order.completed_date
        return 0, None

    def get_due_status(self, vehicle: Vehicle, maintenance_type_id=None,
                       as_of_date=None) -> dict:
        """Return {schedule, status, next_due_km, next_due_date} for the
        first applicable schedule (or the specified maintenance_type_id)."""
        as_of_date = as_of_date or date.today()
        schedules = self._applicable_schedules(vehicle, maintenance_type_id)
        if not schedules:
            return {"schedule": None, "status": "GOOD",
                   "next_due_km": None, "next_due_date": None}

        schedule = schedules[0]
        last_km, last_date = self._last_service(vehicle.id,
                                                schedule.maintenance_type_id)

        due_soon_km = schedule.notify_before_km or self.default_due_soon_km
        due_soon_days = schedule.notify_before_days or self.default_due_soon_days

        next_due_km = None
        next_due_date = None
        status = "GOOD"

        if schedule.trigger_mode in ("KM", "HYBRID") and schedule.interval_km:
            if schedule.next_due_calculation_method == "ORIGINAL_SCHEDULE":
                # Rounds up to the next whole multiple of the interval,
                # ignoring how far past/before the actual completion point
                # was — e.g. completed at 5,280km on a 5,000km interval
                # still schedules the next one at 10,000km, not 10,280km.
                next_due_km = ((last_km // schedule.interval_km) + 1) * schedule.interval_km
            else:  # ACTUAL_COMPLETION (default) / ADMIN_CHOICE (falls back)
                next_due_km = last_km + schedule.interval_km
            current_km = vehicle.current_odometer or 0
            if current_km >= next_due_km:
                status = _worse(status, "OVERDUE")
            elif current_km >= next_due_km - due_soon_km:
                status = _worse(status, "DUE_SOON")

        if schedule.trigger_mode in ("CALENDAR", "HYBRID") and schedule.interval_days:
            base_date = last_date or date.today()
            from datetime import timedelta
            next_due_date = base_date + timedelta(days=schedule.interval_days)
            days_remaining = (next_due_date - as_of_date).days
            if days_remaining <= 0:
                status = _worse(status, "OVERDUE")
            elif days_remaining <= due_soon_days:
                status = _worse(status, "DUE_SOON")

        return {"schedule": schedule, "status": status,
               "next_due_km": next_due_km, "next_due_date": next_due_date}

    def get_all_due_vehicles(self, as_of_date=None) -> list:
        """Return due-status entries for every active vehicle/schedule
        combination that is DUE_SOON or OVERDUE (GOOD entries omitted)."""
        results = []
        vehicles = Vehicle.query.filter_by(is_active=True).all()
        for vehicle in vehicles:
            schedules = self._applicable_schedules(vehicle)
            seen_types = set()
            for schedule in schedules:
                if schedule.maintenance_type_id in seen_types:
                    continue
                seen_types.add(schedule.maintenance_type_id)
                result = self.get_due_status(
                    vehicle, maintenance_type_id=schedule.maintenance_type_id,
                    as_of_date=as_of_date)
                if result["status"] != "GOOD":
                    results.append({**result, "vehicle": vehicle})
        return results
