"""Registration Template service — mirrors PMScheduleService/
PMDueCalculationService's shape for Vehicle Registration renewal."""
from app.extensions import db
from app.modules.registration_config.models import (
    RegistrationTemplate, RegistrationChecklistItem)
from app.modules.system_admin.services.system_parameter_service import (
    SystemParameterService)

DEFAULT_NOTIFY_BEFORE_DAYS = 30


class RegistrationTemplateService:
    def create(self, *, interval_years=3, vehicle_type_id=None,
              vehicle_brand_id=None, vehicle_model_id=None,
              next_generation_policy="AUTO_SCHEDULE",
              notify_before_days=None, priority="MEDIUM", items=None):
        tmpl = RegistrationTemplate(
            interval_years=interval_years, vehicle_type_id=vehicle_type_id,
            vehicle_brand_id=vehicle_brand_id, vehicle_model_id=vehicle_model_id,
            next_generation_policy=next_generation_policy,
            notify_before_days=notify_before_days, priority=priority)
        db.session.add(tmpl)
        db.session.flush()
        for i in (items or []):
            db.session.add(RegistrationChecklistItem(
                template_id=tmpl.id, activity_code=i["activity_code"],
                activity_description=i["activity_description"],
                sort_order=i.get("sort_order", 0)))
        db.session.commit()
        return tmpl

    def update(self, template_id, *, items=None, **kwargs):
        tmpl = db.session.get(RegistrationTemplate, template_id)
        if tmpl is None:
            return None
        for k, v in kwargs.items():
            setattr(tmpl, k, v)
        if items is not None:
            RegistrationChecklistItem.query.filter_by(
                template_id=template_id).delete()
            for i in items:
                db.session.add(RegistrationChecklistItem(
                    template_id=template_id, activity_code=i["activity_code"],
                    activity_description=i["activity_description"],
                    sort_order=i.get("sort_order", 0)))
        db.session.commit()
        return tmpl

    def get_by_id(self, template_id):
        return db.session.get(RegistrationTemplate, template_id)

    def list(self, include_inactive=False):
        q = RegistrationTemplate.query
        if not include_inactive:
            q = q.filter_by(is_active=True)
        return q.all()

    def deactivate(self, template_id):
        tmpl = self.get_by_id(template_id)
        if tmpl:
            tmpl.is_active = False
            db.session.commit()

    def find_applicable(self, vehicle) -> "RegistrationTemplate | None":
        """Most-specific-match-first: Brand+Model, then Vehicle Type,
        then a global (all-vehicles) template — same precedence spirit
        as PM Template matching, just without the free-text fallback
        tier (Registration Templates are newer, so there's no legacy
        free-text data to support)."""
        templates = RegistrationTemplate.query.filter_by(is_active=True).all()
        brand_model_matches = [
            t for t in templates
            if t.vehicle_brand_id and t.vehicle_model_id
            and t.vehicle_brand_id == getattr(vehicle, "vehicle_brand_id", None)
            and t.vehicle_model_id == getattr(vehicle, "vehicle_model_id", None)]
        if brand_model_matches:
            return brand_model_matches[0]
        type_matches = [t for t in templates
                       if t.vehicle_type_id == vehicle.vehicle_type_id]
        if type_matches:
            return type_matches[0]
        global_matches = [t for t in templates
                         if not t.vehicle_type_id and not t.vehicle_brand_id]
        return global_matches[0] if global_matches else None


class RegistrationDueCalculationService:
    def __init__(self):
        params = SystemParameterService()
        default = params.get("REGISTRATION_NOTIFY_BEFORE_DAYS",
                             default=DEFAULT_NOTIFY_BEFORE_DAYS)
        try:
            self.default_notify_before_days = int(default)
        except (TypeError, ValueError):
            self.default_notify_before_days = DEFAULT_NOTIFY_BEFORE_DAYS

    def get_due_status(self, vehicle, as_of_date=None) -> dict:
        from datetime import date as _date
        from app.modules.transactions.vehicle_registration.models import (
            VehicleRegistration)
        as_of_date = as_of_date or _date.today()

        template = RegistrationTemplateService().find_applicable(vehicle)
        last_reg = (VehicleRegistration.query
                   .filter_by(vehicle_id=vehicle.id, status="COMPLETED")
                   .filter(VehicleRegistration.expiry_date.isnot(None))
                   .order_by(VehicleRegistration.expiry_date.desc())
                   .first())

        if last_reg is None:
            return {"status": "NO_RECORD", "template": template,
                   "next_due_date": None, "last_registration": None}

        notify_before_days = (
            template.notify_before_days if template and template.notify_before_days
            else self.default_notify_before_days)
        expiry_date = last_reg.expiry_date
        days_remaining = (expiry_date - as_of_date).days

        if days_remaining < 0:
            status = "OVERDUE"
        elif days_remaining <= notify_before_days:
            status = "DUE_SOON"
        else:
            status = "GOOD"

        return {"status": status, "template": template,
               "next_due_date": expiry_date, "last_registration": last_reg,
               "days_remaining": days_remaining}

    def get_all_due_vehicles(self, as_of_date=None) -> list:
        """Every active vehicle that's DUE_SOON or OVERDUE — mirrors
        PMDueCalculationService.get_all_due_vehicles()'s shape, used by
        both the Dashboard widget and the auto-generation task."""
        from app.modules.master_data.vehicle.models import Vehicle
        results = []
        # Same DISPOSED exclusion as Maintenance PMS — a disposed vehicle
        # has no LTO registration to renew.
        query = Vehicle.query.filter_by(is_active=True).filter(
            Vehicle.status != "DISPOSED")
        for vehicle in query.all():
            result = self.get_due_status(vehicle, as_of_date=as_of_date)
            if result["status"] in ("DUE_SOON", "OVERDUE"):
                results.append({"vehicle": vehicle, **result})
        return results
