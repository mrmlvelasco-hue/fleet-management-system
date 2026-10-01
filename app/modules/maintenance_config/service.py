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

    def generate_series(self, *, maintenance_type_id, trigger_mode, services,
                        vehicle_type_id=None, vehicle_brand_id=None,
                        vehicle_model_id=None, vehicle_make=None,
                        vehicle_model=None, variant=None, engine_type=None,
                        fuel_type=None, transmission=None,
                        model_year_from=None, model_year_to=None,
                        profile_code=None, profile_description=None,
                        effective_date=None,
                        next_pms_generation="AUTO_SCHEDULE",
                        next_due_calculation_method="ACTUAL_COMPLETION",
                        interval_days=None, priority="MEDIUM",
                        notify_before_km=None, notify_before_days=None,
                        escalate_if_overdue=True):
        """Create a whole PMS Profile's service series in one transaction.

        `services` is a list of dicts, each describing one package in
        the series: cumulative_km (the milestone), optionally its own
        interval_km (the repeat step FROM the previous package -- falls
        back to the milestone spacing when omitted), work_description_
        template, and an optional `items` list for that service's own
        checklist. Vehicle matching, maintenance type, trigger mode,
        and every policy/alert field are shared by the whole series --
        this mirrors the approved mock-up, where those are set once at
        the top of the page, not re-entered per service.

        Atomic and validated up front, deliberately: every service is
        checked with _validate_schedule BEFORE any row is added to the
        session, so a bad entry anywhere in the batch raises before a
        single INSERT is attempted, and the one db.session.commit() at
        the end either saves the whole series or nothing does. A
        service's `items` are optional -- PMScopeTemplate already
        refuses zero items (InvalidScopeError), so omitting them simply
        means that service gets no linked checklist, not an error.
        """
        if not services:
            raise InvalidScheduleError("services must not be empty.")

        # Validate every entry BEFORE touching the session, so a bad
        # entry deep in a 37-service batch never leaves the first 30
        # sitting half-added.
        for svc in services:
            _validate_schedule(
                trigger_mode, svc.get("interval_km") or svc.get("cumulative_km"),
                interval_days)

        created = []
        for position, svc in enumerate(services, start=1):
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
                sequence_position=position,
                next_pms_generation=next_pms_generation,
                next_due_calculation_method=next_due_calculation_method,
                maintenance_type_id=maintenance_type_id,
                trigger_mode=trigger_mode,
                interval_km=svc.get("interval_km") or svc.get("cumulative_km"),
                interval_days=interval_days,
                cumulative_km=svc.get("cumulative_km"),
                priority=priority,
                notify_before_km=notify_before_km,
                notify_before_days=notify_before_days,
                escalate_if_overdue=escalate_if_overdue,
                work_description_template=svc.get("work_description_template"))
            db.session.add(sched)
            created.append((sched, svc.get("items"), svc.get("scope_name")))

        db.session.flush()  # assign PKs so scope templates can link by id, before the single commit

        for sched, items, scope_name in created:
            if items:
                tmpl = PMScopeTemplate(
                    maintenance_type_id=maintenance_type_id,
                    name=scope_name or (
                        sched.work_description_template
                        or f"{sched.cumulative_km or sched.interval_km} km service"),
                    pm_schedule_id=sched.id)
                for item in items:
                    tmpl.items.append(PMScopeItem(**item))
                db.session.add(tmpl)

        db.session.commit()
        return [sched for sched, _, _ in created]

    def update_series(self, profile_code, *, maintenance_type_id, trigger_mode,
                      services, **shared_fields):
        """Apply add/edit/remove changes to an existing PMS Profile series
        in one transaction -- the backend for the approved "Edit PMS
        Profile" mock-up.

        Matching is by cumulative_km, the one field that actually
        identifies "the same service" across a save (schedule ids are
        never sent back by the client as part of `services` -- the page
        works in terms of milestones, exactly like creation does):
          - a service in BOTH the existing set and the incoming one is
            updated in place (same row, same id) -- including replacing
            its checklist entirely with whatever `items` now says;
          - a service ONLY in the incoming set is created new;
          - a service ONLY in the existing set is deleted, along with
            its own linked scope template (never left orphaned).

        Every shared field (maintenance type, trigger mode, policy,
        alerts, vehicle matching) applies to every remaining service,
        same as generate_series -- editing these is not per-service.

        Atomic and validated up front: every incoming service is
        checked with _validate_schedule BEFORE any row is touched, so
        one bad entry never leaves the series half-updated.
        """
        existing = {s.cumulative_km: s for s in
                   PMSchedule.query.filter_by(profile_code=profile_code).all()}
        if not existing:
            raise InvalidScheduleError(f"No PMS Profile found for '{profile_code}'.")
        if not services:
            raise InvalidScheduleError("services must not be empty.")

        for svc in services:
            _validate_schedule(
                trigger_mode, svc.get("interval_km") or svc.get("cumulative_km"),
                shared_fields.get("interval_days"))

        incoming_kms = {svc["cumulative_km"] for svc in services}
        for km, sched in existing.items():
            if km not in incoming_kms:
                for tmpl in list(sched.scope_templates):
                    PMScopeItem.query.filter_by(template_id=tmpl.id).delete()
                    db.session.delete(tmpl)
                db.session.delete(sched)

        result = []
        for position, svc in enumerate(services, start=1):
            km = svc["cumulative_km"]
            sched = existing.get(km)
            if sched is None:
                sched = PMSchedule(profile_code=profile_code, cumulative_km=km)
                db.session.add(sched)
            sched.sequence_position = position
            sched.maintenance_type_id = maintenance_type_id
            sched.trigger_mode = trigger_mode
            sched.interval_km = svc.get("interval_km") or km
            sched.work_description_template = svc.get("work_description_template")
            for field in (
                "vehicle_type_id", "vehicle_brand_id", "vehicle_model_id",
                "variant", "engine_type", "fuel_type", "transmission",
                "model_year_from", "model_year_to", "profile_description",
                "effective_date", "next_pms_generation",
                "next_due_calculation_method", "interval_days", "priority",
                "notify_before_km", "notify_before_days", "escalate_if_overdue",
            ):
                if field in shared_fields:
                    setattr(sched, field, shared_fields[field])
            db.session.flush()  # assign a PK for a newly-created row before linking its scope template

            # Checklist for this service is replaced wholesale, not
            # diffed line by line -- simpler and matches how the page
            # itself edits it (the whole ChecklistItemsEditor array is
            # sent back each time, not a per-activity delta).
            existing_tmpl = sched.scope_templates[0] if sched.scope_templates else None
            items = svc.get("items")
            if items:
                if existing_tmpl:
                    PMScopeItem.query.filter_by(template_id=existing_tmpl.id).delete()
                    existing_tmpl.items = [PMScopeItem(**item) for item in items]
                else:
                    tmpl = PMScopeTemplate(
                        maintenance_type_id=maintenance_type_id,
                        name=svc.get("scope_name") or (
                            sched.work_description_template or f"{km} km service"),
                        pm_schedule_id=sched.id)
                    tmpl.items = [PMScopeItem(**item) for item in items]
                    db.session.add(tmpl)
            elif existing_tmpl:
                # Items cleared out entirely -- the service goes back to
                # "no checklist yet", not an empty-but-present one
                # (PMScopeTemplate already refuses zero items).
                PMScopeItem.query.filter_by(template_id=existing_tmpl.id).delete()
                db.session.delete(existing_tmpl)

            result.append(sched)

        db.session.commit()
        return result

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

    def deactivate_profile(self, profile_code) -> int:
        """Deactivate every schedule sharing one profile_code, in one
        commit -- the grouped list shows one row per profile, so its
        single Delete action must act on the whole profile, not just
        whichever schedule happened to be the group's representative
        row (leaving its siblings silently still active).

        Returns the number of schedules deactivated, so the caller can
        tell "deactivated 9" from "nothing matched that code".
        """
        rows = PMSchedule.query.filter_by(
            profile_code=profile_code, is_active=True).all()
        for r in rows:
            r.is_active = False
        db.session.commit()
        return len(rows)

    def list(self, include_inactive=False):
        q = PMSchedule.query.options(
            joinedload(PMSchedule.vehicle_brand),
            joinedload(PMSchedule.vehicle_model_ref),
            joinedload(PMSchedule.vehicle_type),
            joinedload(PMSchedule.maintenance_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def list_paginated(self, page=1, per_page=25, search=None,
                      maintenance_type_id=None, include_inactive=True):
        """One page of PM schedules, with server-side search.

        Same reasoning as the PM Scope Template list: a real VEMS import
        produces thousands of these, and rendering every row produced a
        5.4 MB page the browser then had to parse and index. Search must
        be server-side too -- with one page in the DOM, a client-side box
        would only match the visible page while appearing to search
        everything.
        """
        q = PMSchedule.query.options(
            joinedload(PMSchedule.vehicle_brand),
            joinedload(PMSchedule.vehicle_model_ref),
            joinedload(PMSchedule.vehicle_type),
            joinedload(PMSchedule.maintenance_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if maintenance_type_id:
            q = q.filter(
                PMSchedule.maintenance_type_id == int(maintenance_type_id))
        if search:
            like = f"%{str(search).strip()}%"
            from sqlalchemy import or_
            q = q.filter(or_(
                PMSchedule.profile_description.ilike(like),
                PMSchedule.profile_code.ilike(like),
                PMSchedule.vehicle_make.ilike(like),
                PMSchedule.vehicle_model.ilike(like)))
        pagination = (q.order_by(PMSchedule.profile_code,
                                PMSchedule.sequence_position)
                     .paginate(page=page, per_page=per_page,
                               error_out=False))
        return pagination.items, pagination

    def get_by_id(self, schedule_id):
        return db.session.get(PMSchedule, schedule_id)

    def backfill_missing_profile_codes(self) -> dict:
        """One-time, additive fix for series created via the "New PMS
        Profile" page with Profile Code left blank.

        Confirmed during design: this never touches migrated VEMS data
        -- scripts/import_pm_task_list.py already sets profile_code
        from the source system's own Task_CD, grouping every interval
        of one real-world profile under it before packages are even
        created. Only schedules with profile_code IS NULL are ever
        touched here.

        Grouping key: vehicle match (type/brand/model/make/model/
        variant/engine/fuel/transmission/model years) + maintenance
        type + trigger mode -- the same fields generate_series shares
        across a whole series, so schedules that were genuinely created
        together as one series naturally fall into one group. Each
        group gets ONE generated code applied to every schedule in it:
        BRAND-MODEL-MTYPECODE-N, where N disambiguates multiple blank
        series that would otherwise collide on the same vehicle +
        maintenance type (e.g. two separate HYBRID and KM profiles for
        the same Geely Coolray).

        Safe to run more than once: a schedule already carrying a code
        (including one this method itself assigned on an earlier run)
        is never revisited.
        """
        rows = (PMSchedule.query
               .filter(PMSchedule.profile_code.is_(None))
               .options(joinedload(PMSchedule.vehicle_brand),
                       joinedload(PMSchedule.vehicle_model_ref),
                       joinedload(PMSchedule.maintenance_type))
               .order_by(PMSchedule.id)
               .all())

        groups: dict = {}
        for r in rows:
            key = (r.vehicle_type_id, r.vehicle_brand_id, r.vehicle_model_id,
                  r.vehicle_make, r.vehicle_model, r.variant, r.engine_type,
                  r.fuel_type, r.transmission, r.model_year_from,
                  r.model_year_to, r.maintenance_type_id, r.trigger_mode)
            groups.setdefault(key, []).append(r)

        def _slug(text):
            return "".join(c if c.isalnum() else "-" for c in (text or "").upper()).strip("-") or "VEHICLE"

        used_codes = set()
        groups_fixed = 0
        schedules_updated = 0
        for group_rows in groups.values():
            rep = group_rows[0]
            brand = (rep.vehicle_brand.name if rep.vehicle_brand
                    else rep.vehicle_make) or "VEHICLE"
            model = (rep.vehicle_model_ref.name if rep.vehicle_model_ref
                    else rep.vehicle_model) or ""
            mtype_code = rep.maintenance_type.code if rep.maintenance_type else "PM"
            base = "-".join(filter(None, [_slug(brand), _slug(model), _slug(mtype_code)]))
            n = 1
            while f"{base}-{n}" in used_codes:
                n += 1
            code = f"{base}-{n}"
            used_codes.add(code)
            for r in group_rows:
                r.profile_code = code
            groups_fixed += 1
            schedules_updated += len(group_rows)

        db.session.commit()
        return {"groups_fixed": groups_fixed, "schedules_updated": schedules_updated}

    def list_profiles_paginated(self, page=1, per_page=25, search=None,
                                maintenance_type_id=None, include_inactive=True):
        """One row per PM Profile (schedules sharing a profile_code), or
        one row per standalone schedule (profile_code still NULL --
        never merged with other standalone ones just for sharing NULL).

        Two passes, deliberately, for the same reason list_paginated's
        own docstring already documents: a real VEMS import produces
        thousands of raw schedule rows, and this must not build that
        many full ORM objects (with four eager relationship loads each)
        just to group and paginate them.

        Pass 1 -- lightweight: (id, profile_code) only, for every row
        matching the filters. Grouping and pagination both happen here,
        in Python, over plain tuples -- cheap even at thousands of rows.

        Pass 2 -- only for the current PAGE's representative ids (at
        most `per_page` of them): load the real PMSchedule objects,
        with their eager-loaded relationships, for JSON output.
        """
        q = PMSchedule.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if maintenance_type_id:
            q = q.filter(PMSchedule.maintenance_type_id == int(maintenance_type_id))
        if search:
            like = f"%{str(search).strip()}%"
            from sqlalchemy import or_
            q = q.filter(or_(
                PMSchedule.profile_description.ilike(like),
                PMSchedule.profile_code.ilike(like),
                PMSchedule.vehicle_make.ilike(like),
                PMSchedule.vehicle_model.ilike(like)))

        id_code_rows = (q.with_entities(PMSchedule.id, PMSchedule.profile_code)
                       .order_by(PMSchedule.id).all())

        groups: dict = {}
        order: list = []
        for sid, code in id_code_rows:
            key = code if code is not None else f"__standalone_{sid}__"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(sid)

        total = len(order)
        start = (page - 1) * per_page
        page_keys = order[start:start + per_page]
        representative_ids = [groups[k][0] for k in page_keys]
        package_counts = {groups[k][0]: len(groups[k]) for k in page_keys}

        reps = (PMSchedule.query.options(
                   joinedload(PMSchedule.vehicle_brand),
                   joinedload(PMSchedule.vehicle_model_ref),
                   joinedload(PMSchedule.vehicle_type),
                   joinedload(PMSchedule.maintenance_type))
               .filter(PMSchedule.id.in_(representative_ids))
               .all()) if representative_ids else []
        reps_by_id = {r.id: r for r in reps}
        # Re-ordered to match the page's own group order -- .in_() gives
        # no ordering guarantee of its own.
        ordered_rows = [(reps_by_id[rid], package_counts[rid])
                       for rid in representative_ids if rid in reps_by_id]
        return ordered_rows, total


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

    def list_with_counts(self, include_inactive=False):
        """[(template, activity_count)] for the index page.

        Separate from list() because the index only needs each
        template's activity COUNT, not its activity objects. On a real
        imported catalogue that difference is not academic: measured on
        a 4,626-template / 109,879-item VEMS import, eagerly loading the
        items to render the index took 5.6s and produced 7.1 MB of HTML.
        One GROUP BY aggregate answers the same question without
        materialising a single PMScopeItem.

        list() is left exactly as it was, so any caller that genuinely
        needs the item objects keeps working unchanged.
        """
        from sqlalchemy import func
        counts = dict(
            db.session.query(PMScopeItem.template_id,
                            func.count(PMScopeItem.id))
            .group_by(PMScopeItem.template_id).all())

        q = PMScopeTemplate.query.options(
            joinedload(PMScopeTemplate.maintenance_type),
            joinedload(PMScopeTemplate.pm_schedule).joinedload(
                PMSchedule.vehicle_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return [(t, counts.get(t.id, 0)) for t in q.all()]

    def list_paginated(self, page=1, per_page=25, search=None,
                      maintenance_type_id=None, include_inactive=False):
        """One page of templates, with each one's activity count.

        Server-side rather than letting DataTables page in the browser:
        the whole point is to stop sending 4,626 rows of HTML at all.
        Even after the count fix above, rendering every row still
        produced 5.3 MB per page load and left the browser to index all
        of it.

        Search and the maintenance-type filter are therefore ALSO
        server-side, and that is not optional -- once only one page
        exists in the DOM, a client-side search box would only ever
        match rows on the page you happen to be looking at, which is
        worse than having no search at all.

        Returns (rows, pagination) where rows is [(template, count)].
        """
        from sqlalchemy import func

        q = PMScopeTemplate.query.options(
            joinedload(PMScopeTemplate.maintenance_type),
            joinedload(PMScopeTemplate.pm_schedule).joinedload(
                PMSchedule.vehicle_type))
        if not include_inactive:
            q = q.filter_by(is_active=True)
        if maintenance_type_id:
            q = q.filter(
                PMScopeTemplate.maintenance_type_id == int(maintenance_type_id))
        if search:
            like = f"%{search.strip()}%"
            q = q.filter(PMScopeTemplate.name.ilike(like))

        pagination = (q.order_by(PMScopeTemplate.name)
                     .paginate(page=page, per_page=per_page,
                               error_out=False))

        # Counts for THIS PAGE only -- a GROUP BY across all 109,879
        # items would undo most of the benefit of paging in the first
        # place.
        page_ids = [t.id for t in pagination.items]
        counts = {}
        if page_ids:
            counts = dict(
                db.session.query(PMScopeItem.template_id,
                                func.count(PMScopeItem.id))
                .filter(PMScopeItem.template_id.in_(page_ids))
                .group_by(PMScopeItem.template_id).all())

        rows = [(t, counts.get(t.id, 0)) for t in pagination.items]
        return rows, pagination

    def get_by_id(self, template_id):
        return db.session.get(PMScopeTemplate, template_id)