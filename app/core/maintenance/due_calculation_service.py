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
from app.core.request_cache import request_cached
from app.modules.maintenance_config.models import PMSchedule
from app.modules.master_data.reference.models import MaintenanceType
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.transactions.maintenance_order.models import MaintenanceOrder
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)

DEFAULT_DUE_SOON_KM = 500
DEFAULT_DUE_SOON_DAYS = 30

_STATUS_RANK = {"GOOD": 0, "DUE_SOON": 1, "OVERDUE": 2}


def _worse(a: str, b: str) -> str:
    return a if _STATUS_RANK[a] >= _STATUS_RANK[b] else b


class _Prefetch:
    """In-memory snapshot of everything the due calculation needs.

    Built ONCE per bulk run (a handful of queries), then consulted
    instead of hitting the database inside the per-vehicle loop. The
    matching rules below are a faithful in-memory replay of the query
    logic in the single-vehicle path -- deliberately duplicated rather
    than abstracted, so the single-vehicle path stays byte-for-byte
    unchanged and this optimisation cannot alter any business rule.
    """

    __slots__ = ("schedules_by_id", "active_schedules", "types_by_id",
                 "type_ids_by_name", "last_service", "vehicles_by_id",
                 "historical_untyped", "_brand_ids_by_name", "_model_ids_by_brand_name",
                 "_by_fk", "_by_make_model", "_by_type_generic",
                 "_global_generic", "_match_memo")

    def __init__(self):
        from app.modules.master_data.reference.models import MaintenanceType

        # 1 query -- every active schedule, matched in memory below.
        self.active_schedules = PMSchedule.query.filter_by(
            is_active=True).all()
        # Includes INACTIVE ones: a vehicle's directly-assigned schedule
        # is looked up by id and then checked for is_active, so it has to
        # be resolvable even when inactive to reproduce that check.
        self.schedules_by_id = {s.id: s for s in PMSchedule.query.all()}

        # 1 query -- powers the same-name MaintenanceType fallback.
        types = MaintenanceType.query.all()
        self.types_by_id = {t.id: t for t in types}
        self.type_ids_by_name = {}
        for t in types:
            key = (t.name or "").strip().lower()
            self.type_ids_by_name.setdefault(key, []).append(t.id)

        # 2 small queries -- resolve Vehicle.brand/model text to the real
        # master-data foreign keys used by PMSchedule.vehicle_brand_id and
        # PMSchedule.vehicle_model_id. Vehicle stores the display values
        # (brand/model), not these FK columns, so the bulk path must perform
        # the same resolution as PMDueCalculationService._resolve_vehicle_brand_model_ids().
        from app.modules.master_data.vehicle_brand.models import (
            VehicleBrand, VehicleModel)
        self._brand_ids_by_name = {}
        for b in VehicleBrand.query.all():
            key = (b.name or "").strip().lower()
            self._brand_ids_by_name.setdefault(key, []).append(b.id)

        self._model_ids_by_brand_name = {}
        for m in VehicleModel.query.all():
            key = (m.brand_id, (m.name or "").strip().lower())
            self._model_ids_by_brand_name.setdefault(key, []).append(m.id)

        # 1 query -- latest COMPLETED order per (vehicle, maintenance
        # type). Ordered ascending so the last write per key wins, which
        # reproduces "ORDER BY completed_date DESC LIMIT 1". Rows with no
        # completed_date sort first and are therefore only used when no
        # dated completion exists, matching MySQL's NULL ordering in the
        # original query.
        self.last_service = {}
        rows = (MaintenanceOrder.query
               .filter(MaintenanceOrder.status == "COMPLETED")
               # Portable "NULLs first, then oldest to newest".
               #
               # NOT `.nulls_first()`: that emits the SQL keyword
               # `NULLS FIRST`, which PostgreSQL and SQLite accept but
               # MySQL rejects outright with error 1064. The previous
               # `hasattr(..., "nulls_first")` guard was worthless -- it
               # asks whether SQLAlchemy has the METHOD (always yes),
               # not whether the DATABASE accepts the SQL it generates,
               # so it passed silently and still broke MySQL.
               #
               # Nor can we rely on each dialect's default NULL ordering:
               # MySQL and SQLite sort NULLs first on ASC, PostgreSQL
               # sorts them last. `completed_date IS NULL DESC` states
               # the intent explicitly and compiles to plain, portable
               # SQL everywhere.
               .order_by(MaintenanceOrder.completed_date.is_(None).desc(),
                        MaintenanceOrder.completed_date.asc())
               .all())
        for o in rows:
            if o.maintenance_type_id is None:
                continue
            self.last_service[(o.vehicle_id, o.maintenance_type_id)] = o

        self.vehicles_by_id = {}

        # Historical migration rows created by older versions of the
        # migration importer may have maintenance_type_id=NULL. Keep those
        # rows in memory so the due engine can recover a maintenance-specific
        # baseline from the description (Tire/Battery/Aircon) instead of
        # incorrectly using Vehicle.last_pm_* for every PM type.
        self.historical_untyped = {}
        for o in rows:
            if not getattr(o, "is_historical", False):
                continue
            if o.maintenance_type_id is not None:
                continue
            self.historical_untyped.setdefault(o.vehicle_id, []).append(o)

        # ------------------------------------------------------------------
        # Pre-built match buckets.
        #
        # applicable_schedules() used to LINEAR-SCAN every active schedule
        # for each vehicle -- and again for each (vehicle, maintenance
        # type) pair, because get_due_status() calls it a second time.
        # With 4,626 active schedules and 164 vehicles that is millions of
        # comparisons, each doing .strip().lower() on two strings, which
        # measured at ~9.2 SECONDS to return 3 rows.
        #
        # The matching rules only ever key off a handful of attributes, so
        # every bucket can be built ONCE here (a single pass over the
        # schedules) and looked up in constant time afterwards.
        # ------------------------------------------------------------------
        self._by_fk = {}
        self._by_make_model = {}
        self._by_type_generic = {}
        self._global_generic = []
        for s in self.active_schedules:
            if s.vehicle_brand_id and s.vehicle_model_id:
                self._by_fk.setdefault(
                    (s.vehicle_brand_id, s.vehicle_model_id), []).append(s)
            if s.vehicle_make and s.vehicle_model:
                key = (s.vehicle_make.strip().lower(),
                      s.vehicle_model.strip().lower())
                self._by_make_model.setdefault(key, []).append(s)
            elif not s.vehicle_make and not s.vehicle_model:
                # Generic schedules: by vehicle type, or fully global.
                if s.vehicle_type_id is not None:
                    self._by_type_generic.setdefault(
                        s.vehicle_type_id, []).append(s)
                else:
                    self._global_generic.append(s)

        # applicable_schedules() is called twice for the same
        # (vehicle, maintenance_type) pair on every pass -- once by the
        # caller's loop and once inside get_due_status(). Memoise it.
        self._match_memo = {}

    def applicable_schedules(self, vehicle, maintenance_type_id=None):
        """In-memory equivalent of _applicable_schedules().

        Resolution order is unchanged: an explicitly assigned schedule
        wins; otherwise brand/model FK match, then make/model text match,
        then a vehicle-type generic, then a fully global generic.

        Filtering by maintenance_type is applied to the (small) matched
        bucket rather than to all schedules up front. That is equivalent
        -- intersecting by type and by match is commutative -- but avoids
        walking thousands of rows to discard nearly all of them.
        """
        memo_key = (vehicle.id, maintenance_type_id)
        cached = self._match_memo.get(memo_key)
        if cached is not None:
            return cached

        result = self._resolve_schedules(vehicle, maintenance_type_id)
        self._match_memo[memo_key] = result
        return result

    def _resolve_schedules(self, vehicle, maintenance_type_id=None):
        if vehicle.pm_schedule_id:
            sched = self.schedules_by_id.get(vehicle.pm_schedule_id)
            if sched and sched.is_active and (
                    not maintenance_type_id or
                    sched.maintenance_type_id == maintenance_type_id):
                return [sched]

        def _of_type(candidates):
            if not maintenance_type_id:
                return list(candidates)
            return [s for s in candidates
                   if s.maintenance_type_id == maintenance_type_id]

        # Vehicle does not have vehicle_brand_id / vehicle_model_id columns.
        # Resolve its stored brand/model text to the master-data FKs before
        # checking FK-based PM schedules. Without this, the bulk PMS report
        # can never match schedules such as Tire Replacement that are keyed
        # by vehicle_brand_id + vehicle_model_id.
        brand_key = (getattr(vehicle, "brand", None) or "").strip().lower()
        brand_ids = self._brand_ids_by_name.get(brand_key, ())
        brand_id = brand_ids[0] if brand_ids else None
        model_key = (getattr(vehicle, "model", None) or "").strip().lower()
        model_ids = self._model_ids_by_brand_name.get((brand_id, model_key), ()) if brand_id else ()
        model_id = model_ids[0] if model_ids else None
        if brand_id and model_id:
            fk_matches = _of_type(self._by_fk.get((brand_id, model_id), ()))
            if fk_matches:
                return fk_matches

        key = ((vehicle.brand or "").strip().lower(),
              (vehicle.model or "").strip().lower())
        make_model = _of_type(self._by_make_model.get(key, ()))
        if make_model:
            return make_model

        type_matches = _of_type(
            self._by_type_generic.get(vehicle.vehicle_type_id, ()))
        if type_matches:
            return type_matches

        return _of_type(self._global_generic)

    def last_service_for(self, vehicle, maintenance_type_id):
        """In-memory equivalent of _last_service(), including migration-safe
        handling for historical rows imported before maintenance_type_id was
        captured by the history importer.

        Vehicle.last_pm_* is a LEGACY GENERAL-PMS baseline and therefore is
        only valid for PMS-PREV (maintenance_type_id=1). Tire/Battery/Aircon
        must not inherit that baseline merely because the vehicle came from a
        migration. Older historical rows with no maintenance_type_id are
        recovered conservatively from their descriptions for the independent
        PM types.
        """
        order = self.last_service.get((vehicle.id, maintenance_type_id))
        if order is None:
            target = self.types_by_id.get(maintenance_type_id)
            if target is not None:
                same_name = self.type_ids_by_name.get(
                    (target.name or "").strip().lower(), [])
                if len(same_name) > 1:
                    candidates = [
                        self.last_service.get((vehicle.id, tid))
                        for tid in same_name]
                    candidates = [c for c in candidates if c is not None]
                    if candidates:
                        order = max(
                            candidates,
                            key=lambda o: (o.completed_date is not None,
                                          o.completed_date))

        if order is None and maintenance_type_id in (2, 3, 4):
            keywords = {
                2: ("tire", "tyre"),
                3: ("battery",),
                4: ("aircon", "air-con", "air conditioning", "air-conditioning"),
            }.get(maintenance_type_id, ())
            candidates = []
            for historical in self.historical_untyped.get(vehicle.id, ()):
                description = (historical.description or "").strip().lower()
                if description and any(k in description for k in keywords):
                    candidates.append(historical)
            if candidates:
                order = max(
                    candidates,
                    key=lambda o: (
                        o.completed_date is not None,
                        o.completed_date or date.min))

        if order:
            if order.odometer_at_service is not None:
                return order.odometer_at_service, order.completed_date
            return (vehicle.current_odometer or 0), order.completed_date

        # last_pm_* is explicitly a legacy GENERAL PMS baseline. Never reuse
        # it as the baseline for independent PM types.
        if maintenance_type_id == 1 and (
                vehicle.last_pm_odometer is not None
                or vehicle.last_pm_date is not None):
            return vehicle.last_pm_odometer or 0, vehicle.last_pm_date
        return 0, None


#: Vehicle statuses that still attract preventive maintenance and LTO
#: registration. DISPOSED and INACTIVE are out -- a unit retired or taken
#: out of service needs neither. IN_REPAIR stays in: a vehicle in the shop
#: is exactly one that maintenance and registration still apply to.
#: Shared so the PM and Registration due calculations cannot drift apart.
DUE_ELIGIBLE_STATUSES = ("ACTIVE", "IN_REPAIR")


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
        if order is None:
            # Strict ID match found nothing — this is exactly the
            # reported bug: a Brand+Model-specific schedule can resolve
            # to a DIFFERENT MaintenanceType row than whichever one the
            # actually-completed order used, even when both display the
            # identical name (e.g. one from a VEMS import, one created
            # separately by hand) — same real-world maintenance concept,
            # different database row. Fall back to matching by name
            # (case-insensitive) so a genuine completion isn't invisible
            # to due-calculation just because of which specific row got
            # selected at MO-creation time. Deliberately NOT falling back
            # to matching by category alone — that's broad enough to
            # wrongly conflate genuinely different maintenance concepts
            # (e.g. "Oil Change PM" and "Tire Rotation PM" both being
            # category PM), which name-matching avoids.
            target_type = db.session.get(MaintenanceType, maintenance_type_id)
            if target_type is not None:
                same_name_ids = [
                    mt.id for mt in MaintenanceType.query.all()
                    if mt.name.strip().lower() == target_type.name.strip().lower()]
                if len(same_name_ids) > 1:
                    order = (MaintenanceOrder.query
                            .filter(MaintenanceOrder.vehicle_id == vehicle_id,
                                   MaintenanceOrder.maintenance_type_id.in_(same_name_ids),
                                   MaintenanceOrder.status == "COMPLETED")
                            .order_by(MaintenanceOrder.completed_date.desc())
                            .first())
        if order:
            if order.odometer_at_service is not None:
                return order.odometer_at_service, order.completed_date
            # The completing user left Odometer at Service blank — use the
            # vehicle's current odometer as the best available proxy.
            vehicle = db.session.get(Vehicle, vehicle_id)
            fallback_km = (vehicle.current_odometer or 0) if vehicle else 0
            return fallback_km, order.completed_date

        # Older history migrations created COMPLETED orders without a
        # maintenance_type_id. Recover an independent PM baseline from the
        # description when it is explicit enough to identify Tire/Battery/
        # Aircon. This keeps migrated history useful without guessing across
        # unrelated maintenance types.
        if maintenance_type_id in (2, 3, 4):
            keywords = {
                2: ("tire", "tyre"),
                3: ("battery",),
                4: ("aircon", "air-con", "air conditioning", "air-conditioning"),
            }[maintenance_type_id]
            candidates = (MaintenanceOrder.query
                          .filter_by(vehicle_id=vehicle_id,
                                    maintenance_type_id=None,
                                    status="COMPLETED",
                                    is_historical=True)
                          .all())
            matched = [
                o for o in candidates
                if any(k in (o.description or "").strip().lower()
                       for k in keywords)]
            if matched:
                order = max(
                    matched,
                    key=lambda o: (
                        o.completed_date is not None,
                        o.completed_date or date.min))
                if order.odometer_at_service is not None:
                    return order.odometer_at_service, order.completed_date
                vehicle = db.session.get(Vehicle, vehicle_id)
                fallback_km = (vehicle.current_odometer or 0) if vehicle else 0
                return fallback_km, order.completed_date

        # last_pm_* is a GENERAL PMS migration baseline. It applies only to
        # Preventive Maintenance Service itself (maintenance_type_id=1).
        vehicle = db.session.get(Vehicle, vehicle_id)
        if (maintenance_type_id == 1 and vehicle and
                (vehicle.last_pm_odometer is not None
                 or vehicle.last_pm_date is not None)):
            return vehicle.last_pm_odometer or 0, vehicle.last_pm_date
        return 0, None

    def get_due_status(self, vehicle: Vehicle, maintenance_type_id=None,
                       as_of_date=None, _prefetch=None) -> dict:
        """Return {schedule, status, next_due_km, next_due_date} for the
        first applicable schedule (or the specified maintenance_type_id).

        `_prefetch` is an internal optimisation used by the bulk path
        (get_all_due_vehicles): when supplied, reference data is read
        from memory instead of the database, turning what was ~5 queries
        PER VEHICLE into zero. Omitted everywhere else, so the
        single-vehicle path behaves exactly as before.
        """
        as_of_date = as_of_date or date.today()
        if _prefetch is not None:
            schedules = _prefetch.applicable_schedules(vehicle,
                                                       maintenance_type_id)
        else:
            schedules = self._applicable_schedules(vehicle,
                                                   maintenance_type_id)
        if not schedules:
            return {"schedule": None, "status": "GOOD",
                   "next_due_km": None, "next_due_date": None}

        schedule = schedules[0]
        if _prefetch is not None:
            last_km, last_date = _prefetch.last_service_for(
                vehicle, schedule.maintenance_type_id)
        else:
            last_km, last_date = self._last_service(
                vehicle.id, schedule.maintenance_type_id)

        # An explicit notify_before_km/days on the schedule is a
        # deliberate admin choice and is used as-is, uncapped. But the
        # SYSTEM DEFAULT (30 days / 500 km) was flagging DUE_SOON on the
        # very same day a short-interval service was completed — e.g. a
        # 30-day interval schedule with no explicit notify_before_days
        # got the full 30-day default window, meaning the vehicle was
        # "due soon" for its entire service life between completions,
        # not just when actually approaching due. Capping the default to
        # roughly a third of the schedule's own interval keeps the
        # warning meaningful regardless of how short the interval is,
        # without shrinking an already-reasonable default for genuinely
        # long intervals (a year-long schedule still gets the full
        # 30-day default, since that's well under a third of 365 days).
        if schedule.notify_before_days:
            due_soon_days = schedule.notify_before_days
        elif schedule.interval_days:
            due_soon_days = min(self.default_due_soon_days,
                               max(1, schedule.interval_days // 3))
        else:
            due_soon_days = self.default_due_soon_days

        if schedule.notify_before_km:
            due_soon_km = schedule.notify_before_km
        elif schedule.interval_km:
            due_soon_km = min(self.default_due_soon_km,
                             max(1, schedule.interval_km // 3))
        else:
            due_soon_km = self.default_due_soon_km

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

    @request_cached("pm_all_due_vehicles")
    def get_all_due_vehicles(self, as_of_date=None,
                             exclude_with_open_order=True) -> list:
        """Return due-status entries for every active vehicle/schedule
        combination that is DUE_SOON or OVERDUE (GOOD entries omitted).

        By default, a vehicle that ALREADY has an open (not COMPLETED /
        CANCELLED) Maintenance Order for that maintenance type is left
        out: the work is already raised, so listing it again as "due"
        double-counts it, and -- since creating a second open PM order
        for the same vehicle is refused -- the dashboard link would dead-
        end on an error the person can do nothing about. Pass
        exclude_with_open_order=False for a raw due calculation that
        ignores whether the work has been raised yet.
        """
        from app.modules.transactions.maintenance_order.models import (
            MaintenanceOrder)
        from sqlalchemy.orm import joinedload

        open_pairs = set()
        if exclude_with_open_order:
            open_pairs = {
                (o.vehicle_id, o.maintenance_type_id)
                for o in MaintenanceOrder.query
                .filter(MaintenanceOrder.status.notin_(
                    ["COMPLETED", "CANCELLED"]))
                .filter(MaintenanceOrder.maintenance_type_id.isnot(None))
                .all()}

        # Build the in-memory snapshot ONCE (a handful of queries)
        # instead of re-querying reference data for every vehicle. With
        # ~5 queries per vehicle before, a 164-vehicle fleet issued
        # roughly 800 round trips from this method alone; it is now a
        # fixed cost regardless of fleet size.
        prefetch = _Prefetch()

        results = []
        # ACTIVE and IN_REPAIR.
        #
        # This excluded DISPOSED alone, which also let INACTIVE through --
        # a unit taken out of service is not one to schedule work on.
        # IN_REPAIR is deliberately KEPT: a vehicle in the shop is
        # precisely one that maintenance and registration still apply
        # to, and dropping it would hide work that is actively in hand.
        #
        # is_active is a different axis (the soft-delete flag) and is
        # still required: disposal is a business STATUS, not a delete,
        # so neither check implies the other.
        #
        # branch/vehicle_type are eager-loaded because the dashboard
        # template renders them per row.
        vehicles = (Vehicle.query
                   .options(joinedload(Vehicle.branch),
                           joinedload(Vehicle.vehicle_type))
                   .filter_by(is_active=True)
                   .filter(Vehicle.status.in_(DUE_ELIGIBLE_STATUSES)).all())

        # IMPORTANT: do not call applicable_schedules(vehicle) with no
        # maintenance_type_id here. That method intentionally returns the
        # single most-specific schedule bucket for the *first* applicable
        # maintenance type. Using it as the bulk-report source makes a
        # vehicle's assigned PMS schedule hide independent schedules such
        # as Tire Management, Battery, etc.
        #
        # The bulk report must resolve schedules independently PER
        # maintenance type. This preserves precedence for the vehicle's
        # assigned PMS type while still allowing a generic/type-specific
        # Tire schedule to be evaluated when the assigned schedule belongs
        # to a different maintenance type.
        active_type_ids = {
            s.maintenance_type_id for s in prefetch.active_schedules
            if s.maintenance_type_id is not None
        }

        for vehicle in vehicles:
            # Include the directly assigned schedule's type even if an
            # unusual data state means it is not represented in the active
            # schedule snapshot (e.g. an inactive assigned schedule).
            assigned = prefetch.schedules_by_id.get(vehicle.pm_schedule_id)
            type_ids = set(active_type_ids)
            if assigned and assigned.maintenance_type_id is not None:
                type_ids.add(assigned.maintenance_type_id)

            seen_types = set()
            for maintenance_type_id in type_ids:
                if maintenance_type_id in seen_types:
                    continue
                seen_types.add(maintenance_type_id)

                schedules = prefetch.applicable_schedules(
                    vehicle, maintenance_type_id)
                if not schedules:
                    continue

                if (vehicle.id, maintenance_type_id) in open_pairs:
                    continue  # already raised — not actionable again

                result = self.get_due_status(
                    vehicle, maintenance_type_id=maintenance_type_id,
                    as_of_date=as_of_date, _prefetch=prefetch)
                if result["status"] != "GOOD":
                    results.append({**result, "vehicle": vehicle})
        return results
