"""Business rules for PM Schedule and PM Scope Template configuration."""
from app.extensions import db
from sqlalchemy.orm import joinedload, selectinload
from app.modules.maintenance_config.models import (
    PMSchedule, PMScopeTemplate, PMScopeItem)


class InvalidScheduleError(Exception):
    pass


class InvalidScopeError(Exception):
    pass


def _validate_schedule(trigger_mode, interval_km, interval_days):
    if trigger_mode == "KM" and not interval_km:
        raise InvalidScheduleError("KM trigger requires interval_km.")
    if trigger_mode == "CALENDAR" and not interval_days:
        raise InvalidScheduleError("CALENDAR trigger requires interval_days.")
    if trigger_mode == "HYBRID" and not (interval_km and interval_days):
        raise InvalidScheduleError(
            "HYBRID trigger requires both interval_km and interval_days.")


class PMScheduleService:
    def list_applicable_for_criteria(self, *, brand_name=None, model_name=None,
                                     vehicle_type_id=None, maintenance_type_id=None):
        """Same matching precedence as PMDueCalculationService's
        _applicable_schedules(), but driven by raw criteria instead of a
        saved Vehicle record — used by the Vehicle form's 'Assigned PM
        Template' dropdown, which needs to filter live as Brand/Model/
        Vehicle Type are being typed, before the vehicle even exists.
        Checks BOTH real FK Brand+Model matches (vehicle_brand_id/
        vehicle_model_id — how most VEMS-imported templates are stored)
        and free-text vehicle_make/vehicle_model matches, since a
        schedule could legitimately be stored either way."""
        if not brand_name and not model_name and not vehicle_type_id:
            return []

        base_query = PMSchedule.query.filter_by(is_active=True)
        if maintenance_type_id:
            base_query = base_query.filter_by(maintenance_type_id=maintenance_type_id)

        brand = (brand_name or "").strip().lower()
        model = (model_name or "").strip().lower()
        if brand and model:
            from app.modules.master_data.vehicle_brand.models import (
                VehicleBrand, VehicleModel)
            brand_row = VehicleBrand.query.filter(
                db.func.lower(VehicleBrand.name) == brand).first()
            fk_matches = []
            if brand_row:
                model_row = VehicleModel.query.filter(
                    VehicleModel.brand_id == brand_row.id,
                    db.func.lower(VehicleModel.name) == model).first()
                if model_row:
                    fk_matches = base_query.filter_by(
                        vehicle_brand_id=brand_row.id,
                        vehicle_model_id=model_row.id).all()
            if fk_matches:
                return fk_matches

            make_model_matches = [
                s for s in base_query.all()
                if s.vehicle_make and s.vehicle_model
                and s.vehicle_make.strip().lower() == brand
                and s.vehicle_model.strip().lower() == model]
            if make_model_matches:
                return make_model_matches

        if vehicle_type_id:
            type_matches = base_query.filter_by(vehicle_type_id=vehicle_type_id).all()
            if type_matches:
                return type_matches

        return base_query.filter_by(vehicle_type_id=None, vehicle_make=None,
                                    vehicle_model=None).all()

    def create(self, *, maintenance_type_id, trigger_mode,
               vehicle_type_id=None, vehicle_make=None, vehicle_model=None,
               vehicle_brand_id=None, vehicle_model_id=None,
               variant=None, engine_type=None, fuel_type=None,
               transmission=None, model_year_from=None, model_year_to=None,
               profile_code=None, profile_description=None,
               effective_date=None, sequence_position=None,
               next_pms_generation="AUTO_SCHEDULE",
               next_due_calculation_method="ACTUAL_COMPLETION",
               interval_km=None, interval_days=None, interval_hours=None,
               cumulative_km=None,
               priority="MEDIUM", notify_before_km=None,
               notify_before_days=None, escalate_if_overdue=True,
               work_description_template=None):
        _validate_schedule(trigger_mode, interval_km, interval_days)
        sched = PMSchedule(
            vehicle_type_id=vehicle_type_id,
            vehicle_make=(vehicle_make or "").strip() or None,
            vehicle_model=(vehicle_model or "").strip() or None,
            vehicle_brand_id=vehicle_brand_id,
            vehicle_model_id=vehicle_model_id,
            variant=variant, engine_type=engine_type, fuel_type=fuel_type,
            transmission=transmission, model_year_from=model_year_from,
            model_year_to=model_year_to, profile_code=profile_code,
            profile_description=profile_description,
            effective_date=effective_date,
            sequence_position=sequence_position,
            next_pms_generation=next_pms_generation,
            next_due_calculation_method=next_due_calculation_method,
            maintenance_type_id=maintenance_type_id,
            trigger_mode=trigger_mode, interval_km=interval_km,
            interval_days=interval_days, interval_hours=interval_hours,
            cumulative_km=cumulative_km,
            priority=priority,
            notify_before_km=notify_before_km,
            notify_before_days=notify_before_days,
            escalate_if_overdue=escalate_if_overdue,
            work_description_template=work_description_template)
        db.session.add(sched)
        db.session.commit()
        return sched

    def update(self, schedule_id, **kwargs):
        sched = db.session.get(PMSchedule, schedule_id)
        if sched is None:
            return None
        merged = {
            "trigger_mode": kwargs.get("trigger_mode", sched.trigger_mode),
            "interval_km": kwargs.get("interval_km", sched.interval_km),
            "interval_days": kwargs.get("interval_days", sched.interval_days),
        }
        _validate_schedule(**merged)
        if "vehicle_make" in kwargs:
            kwargs["vehicle_make"] = (kwargs["vehicle_make"] or "").strip() or None
        if "vehicle_model" in kwargs:
            kwargs["vehicle_model"] = (kwargs["vehicle_model"] or "").strip() or None
        for k, v in kwargs.items():
            setattr(sched, k, v)
        db.session.commit()
        return sched

    def deactivate(self, schedule_id):
        sched = db.session.get(PMSchedule, schedule_id)
        if sched:
            sched.is_active = False
            db.session.commit()

    def list(self, include_inactive=False):
        q = PMSchedule.query.options(
            joinedload(PMSchedule.vehicle_brand),
            joinedload(PMSchedule.vehicle_model_ref),
            joinedload(PMSchedule.vehicle_type),
            joinedload(PMSchedule.maintenance_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def get_by_id(self, schedule_id):
        return db.session.get(PMSchedule, schedule_id)


class PMSProfileService:
    """PMS-2: a 'Profile' is simply the group of PMSchedule rows (packages)
    sharing the same profile_code — no separate parent table. Each package
    keeps its own independent recurring interval and is due-calculated
    exactly like any other PMSchedule (PMDueCalculationService needs zero
    changes for this); Profile grouping is purely an organizational/display
    concern layered on top."""

    def list_profiles(self) -> list:
        rows = (PMSchedule.query
               .filter(PMSchedule.profile_code.isnot(None))
               .filter_by(is_active=True)
               .all())
        grouped = {}
        for r in rows:
            g = grouped.setdefault(r.profile_code, {
                "profile_code": r.profile_code,
                "description": r.profile_description,
                "vehicle_brand": r.vehicle_brand,
                "vehicle_model_ref": r.vehicle_model_ref,
                "package_count": 0,
            })
            g["package_count"] += 1
        return list(grouped.values())

    def get_profile(self, profile_code: str) -> list:
        # Sorted in Python rather than via SQL's nullslast() — that
        # construct has no native equivalent on MySQL (only PostgreSQL/
        # Oracle support "NULLS LAST" directly), so it worked fine
        # against our SQLite test database but risked a genuine SQL
        # error on a real MySQL database. A profile's package count is
        # always small (a handful, rarely more than a few dozen), so
        # sorting after fetching is cheap and fully portable.
        rows = (PMSchedule.query
               .filter_by(profile_code=profile_code, is_active=True)
               .all())
        return sorted(rows, key=lambda r: (
            r.sequence_position is None, r.sequence_position or 0,
            r.interval_km is None, r.interval_km or 0))


class PMScopeTemplateService:
    def list_applicable_for_vehicle(self, vehicle, maintenance_type_id=None) -> list:
        """The scope templates actually relevant to THIS vehicle — via
        its matched PM Schedule(s) (Brand+Model, then Vehicle Type, then
        global — same precedence as PMDueCalculationService), not the
        entire global list. Fixes the reported bug where selecting a
        Ford Escape on the Maintenance Order form showed an unrelated
        Honda City template too."""
        from app.core.maintenance.due_calculation_service import (
            PMDueCalculationService)
        schedules = PMDueCalculationService()._applicable_schedules(
            vehicle, maintenance_type_id)
        seen_ids = set()
        results = []
        for schedule in schedules:
            for tmpl in schedule.scope_templates:
                if tmpl.id not in seen_ids and tmpl.is_active:
                    seen_ids.add(tmpl.id)
                    results.append(tmpl)
        return results

    def get_next_due_scope_template(self, vehicle, maintenance_type_id=None):
        """Among this vehicle's applicable schedules, the scope template
        of the specific PACKAGE that's actually next due for THIS
        vehicle (correctly sequenced within the PMS Profile cycle -- the
        package AFTER the last completed one, not just the first schedule
        that generically applies) -- so a fleet admin creating an MO
        manually gets the right default auto-selected."""
        rec = self.get_next_due_recommendation(vehicle, maintenance_type_id)
        pkg = rec.get("recommended_package") if rec else None
        if pkg is None or not pkg.scope_templates:
            return None
        return pkg.scope_templates[0]

    def get_next_due_recommendation(self, vehicle, maintenance_type_id=None):
        """Full structured recommendation (package, status, due-by, due
        odometer/date, reason) for the vehicle's next PM package -- used
        both to auto-select the scope template and to show the fleet
        admin WHY it was selected on the MO form."""
        from app.core.maintenance.pm_package_recommendation_service import (
            PMPackageRecommendationService)
        return PMPackageRecommendationService().recommend(
            vehicle, maintenance_type_id=maintenance_type_id)

    def create(self, *, maintenance_type_id, name, items,
               description=None, pm_schedule_id=None):
        if not items:
            raise InvalidScopeError(
                "A scope template must have at least one activity item.")
        tmpl = PMScopeTemplate(maintenance_type_id=maintenance_type_id,
                               name=name, description=description,
                               pm_schedule_id=pm_schedule_id)
        db.session.add(tmpl)
        for item in items:
            tmpl.items.append(PMScopeItem(**item))
        db.session.commit()
        return tmpl

    def update(self, template_id, *, name=None, description=None, items=None,
               pm_schedule_id=None):
        tmpl = db.session.get(PMScopeTemplate, template_id)
        if tmpl is None:
            return None
        if name is not None:
            tmpl.name = name
        if description is not None:
            tmpl.description = description
        if pm_schedule_id is not None:
            tmpl.pm_schedule_id = pm_schedule_id
        if items is not None:
            if not items:
                raise InvalidScopeError(
                    "A scope template must have at least one activity item.")
            tmpl.items.clear()
            db.session.flush()
            for item in items:
                tmpl.items.append(PMScopeItem(**item))
        db.session.commit()
        return tmpl

    def deactivate(self, template_id):
        tmpl = db.session.get(PMScopeTemplate, template_id)
        if tmpl:
            tmpl.is_active = False
            db.session.commit()

    def list(self, include_inactive=False):
        q = PMScopeTemplate.query.options(
            joinedload(PMScopeTemplate.maintenance_type),
            selectinload(PMScopeTemplate.items),
            joinedload(PMScopeTemplate.pm_schedule).joinedload(
                PMSchedule.vehicle_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def get_by_id(self, template_id):
        return db.session.get(PMScopeTemplate, template_id)
