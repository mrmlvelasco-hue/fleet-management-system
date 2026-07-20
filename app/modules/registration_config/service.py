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
        from app.modules.registration_config.lto_plate_schedule import (
            get_plate_schedule, calculate_due_date_from_plate,
            next_due_date_from_plate)
        as_of_date = as_of_date or _date.today()

        template = RegistrationTemplateService().find_applicable(vehicle)
        plate_number = vehicle.plate_number or vehicle.conduction_number
        schedule = get_plate_schedule(plate_number)
        lto_month = schedule["month"] if schedule else None
        lto_week = schedule["week"] if schedule else None
        notify_before_days = (
            template.notify_before_days if template and template.notify_before_days
            else self.default_notify_before_days)

        def _status_from_expiry(expiry_date):
            days_remaining = (expiry_date - as_of_date).days
            if days_remaining < 0:
                status = "OVERDUE"
            elif days_remaining <= notify_before_days:
                status = "DUE_SOON"
            else:
                status = "GOOD"
            calculated = (calculate_due_date_from_plate(
                plate_number, expiry_date.year) if schedule else None)
            warning = None
            if calculated is not None:
                # LTO renewal weeks are ~7-day blocks; a gap bigger than
                # two blocks (14 days) is a meaningful mismatch worth
                # flagging, rather than every few-day rounding difference.
                if abs((calculated - expiry_date).days) > 14:
                    warning = "REGISTRATION_DATE_MISMATCH"
            return status, calculated, warning, days_remaining

        last_reg = (VehicleRegistration.query
                   .filter_by(vehicle_id=vehicle.id, status="COMPLETED")
                   .filter(VehicleRegistration.expiry_date.isnot(None))
                   .order_by(VehicleRegistration.expiry_date.desc())
                   .first())

        if last_reg is not None:
            # A COMPLETED registration exists — it's the source of truth
            # for due-status. The plate schedule is only cross-checked
            # against it to flag a possible data-entry mistake.
            status, calculated, warning, days_remaining = _status_from_expiry(
                last_reg.expiry_date)
            return {
                "status": status, "source": "REGISTRATION_RECORD",
                "template": template, "next_due_date": last_reg.expiry_date,
                "last_registration": last_reg, "lto_month": lto_month,
                "lto_week": lto_week, "calculated_due_date": calculated,
                "stored_expiry_date": last_reg.expiry_date,
                "days_remaining": days_remaining, "warning": warning,
            }

        if vehicle.last_known_registration_expiry is not None:
            # No digitized VehicleRegistration transaction yet, but this
            # vehicle's actual current expiry IS known (entered once,
            # manually, typically during data migration from a legacy
            # system) — use it as a real trigger instead of only being
            # able to guess from the plate's LTO schedule, which can be
            # off by up to a year if the last actual renewal date isn't
            # known. Once a real COMPLETED registration is recorded, that
            # takes over automatically (see the branch above).
            status, calculated, warning, days_remaining = _status_from_expiry(
                vehicle.last_known_registration_expiry)
            return {
                "status": status, "source": "MANUAL_ENTRY",
                "template": template,
                "next_due_date": vehicle.last_known_registration_expiry,
                "last_registration": None, "lto_month": lto_month,
                "lto_week": lto_week, "calculated_due_date": calculated,
                "stored_expiry_date": vehicle.last_known_registration_expiry,
                "days_remaining": days_remaining, "warning": warning,
            }

        # Neither a digitized registration nor a manually-entered known
        # expiry exists — fall back to the plate's LTO schedule alone so
        # a reminder can still be generated instead of the vehicle
        # silently having no due date tracked at all.
        suggested = (next_due_date_from_plate(plate_number, as_of_date)
                    if schedule else None)
        return {
            "status": "NO_RECORD", "source": "PLATE_SCHEDULE",
            "template": template, "next_due_date": suggested,
            "suggested_due_date": suggested, "last_registration": None,
            "lto_month": lto_month, "lto_week": lto_week,
            "calculated_due_date": suggested, "stored_expiry_date": None,
            "days_remaining": (suggested - as_of_date).days if suggested else None,
            "warning": None,
        }

    def get_all_due_vehicles(self, as_of_date=None, statuses=None) -> list:
        """Every active vehicle whose status is in `statuses` — defaults
        to (DUE_SOON, OVERDUE), preserving the exact prior behavior for
        the Dashboard widget and the auto-generation task. Pass e.g.
        statuses=("NO_RECORD",) for the "No Registration Record" filter,
        or statuses=("OVERDUE",) for "Expired Registration"."""
        from app.modules.master_data.vehicle.models import Vehicle
        statuses = statuses or ("DUE_SOON", "OVERDUE")
        results = []
        # Same DISPOSED exclusion as Maintenance PMS — a disposed vehicle
        # has no LTO registration to renew.
        query = Vehicle.query.filter_by(is_active=True).filter(
            Vehicle.status != "DISPOSED")
        for vehicle in query.all():
            result = self.get_due_status(vehicle, as_of_date=as_of_date)
            if result["status"] in statuses:
                results.append({"vehicle": vehicle, **result})
        return results
