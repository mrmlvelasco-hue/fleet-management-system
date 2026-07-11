"""Business rules for PM Schedule and PM Scope Template configuration."""
from app.extensions import db
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
    def create(self, *, maintenance_type_id, trigger_mode,
               vehicle_type_id=None, interval_km=None, interval_days=None,
               priority="MEDIUM"):
        _validate_schedule(trigger_mode, interval_km, interval_days)
        sched = PMSchedule(
            vehicle_type_id=vehicle_type_id,
            maintenance_type_id=maintenance_type_id,
            trigger_mode=trigger_mode, interval_km=interval_km,
            interval_days=interval_days, priority=priority)
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
        q = PMSchedule.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def get_by_id(self, schedule_id):
        return db.session.get(PMSchedule, schedule_id)


class PMScopeTemplateService:
    def create(self, *, maintenance_type_id, name, items, description=None):
        if not items:
            raise InvalidScopeError(
                "A scope template must have at least one activity item.")
        tmpl = PMScopeTemplate(maintenance_type_id=maintenance_type_id,
                               name=name, description=description)
        db.session.add(tmpl)
        for item in items:
            tmpl.items.append(PMScopeItem(**item))
        db.session.commit()
        return tmpl

    def update(self, template_id, *, name=None, description=None, items=None):
        tmpl = db.session.get(PMScopeTemplate, template_id)
        if tmpl is None:
            return None
        if name is not None:
            tmpl.name = name
        if description is not None:
            tmpl.description = description
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
        q = PMScopeTemplate.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def get_by_id(self, template_id):
        return db.session.get(PMScopeTemplate, template_id)
