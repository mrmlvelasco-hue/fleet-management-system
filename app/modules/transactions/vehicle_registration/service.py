"""Vehicle Registration service — Philippine LTO rules: 3-year validity
for NEW registrations, 1-year for RENEWAL, Conduction Number before Plate
Number (plate is assigned to the vehicle only on registration completion),
plus expiring-registration detection for renewal reminders."""
from dateutil.relativedelta import relativedelta

from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.vehicle_registration.models import (
    VehicleRegistration)
from app.modules.master_data.vehicle.service import VehicleService

DEFAULT_VALIDITY_YEARS = {"NEW": 3, "RENEWAL": 1}


class DuplicateActiveRegistrationError(Exception):
    pass


class NoExistingRegistrationError(Exception):
    pass


class InvalidRegistrationStateError(Exception):
    pass


class VehicleRegistrationService(BaseTransactionService):
    model = VehicleRegistration
    document_type_code = "VR"
    reference_table = "vehicle_registrations"

    def create(self, *, vehicle_id, registration_type, registration_date,
               user, validity_years=None, or_cr_cost=None,
               odometer_at_registration=None):
        existing = (VehicleRegistration.query
                   .filter_by(vehicle_id=vehicle_id)
                   .filter(VehicleRegistration.status != "CANCELLED")
                   .order_by(VehicleRegistration.id.desc())
                   .all())

        if registration_type == "NEW":
            active = [r for r in existing
                     if r.expiry_date is None or r.expiry_date >= registration_date]
            if active:
                raise DuplicateActiveRegistrationError(
                    "This vehicle already has an active or pending "
                    "registration; a second NEW registration is not allowed.")
        elif registration_type == "RENEWAL":
            if not existing:
                raise NoExistingRegistrationError(
                    "A RENEWAL requires an existing prior registration "
                    "for this vehicle.")

        validity_years = validity_years or DEFAULT_VALIDITY_YEARS.get(
            registration_type, 1)
        expiry_date = registration_date + relativedelta(years=validity_years)

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        reg = VehicleRegistration(
            document_number=doc_number, vehicle_id=vehicle_id,
            registration_type=registration_type,
            registration_date=registration_date,
            validity_years=validity_years, expiry_date=expiry_date,
            or_cr_cost=or_cr_cost,
            odometer_at_registration=odometer_at_registration,
            status="DRAFT", requested_by=user.id if user else None)
        db.session.add(reg)
        db.session.flush()

        from app.modules.registration_config.service import (
            RegistrationTemplateService)
        from app.modules.transactions.vehicle_registration.models import (
            RegistrationTransactionChecklistItem)
        vehicle = VehicleService().get(vehicle_id)
        template = (RegistrationTemplateService().find_applicable(vehicle)
                   if vehicle else None)
        if template and template.checklist_items:
            for i in template.checklist_items:
                db.session.add(RegistrationTransactionChecklistItem(
                    registration_id=reg.id, activity_code=i.activity_code,
                    activity_description=i.activity_description,
                    sort_order=i.sort_order))

        db.session.commit()
        return reg

    def toggle_checklist_item(self, item_id: int, done: bool, user):
        from datetime import datetime, timezone
        from app.modules.transactions.vehicle_registration.models import (
            RegistrationTransactionChecklistItem)
        item = db.session.get(RegistrationTransactionChecklistItem, item_id)
        if item.registration.status == "COMPLETED":
            raise InvalidRegistrationStateError(
                "Checklist items can no longer be updated once the "
                "registration is completed.")
        item.is_done = done
        item.done_by = user.id if done and user else None
        item.done_at = datetime.now(timezone.utc) if done else None
        db.session.commit()
        return item

    def complete(self, registration_id: int, *, or_number, cr_number,
                plate_number=None):
        reg = db.session.get(VehicleRegistration, registration_id)
        reg.or_number = or_number
        reg.cr_number = cr_number
        if plate_number:
            reg.plate_number = plate_number
            VehicleService().assign_plate(reg.vehicle_id, plate_number)
        reg.status = "COMPLETED"
        db.session.commit()
        return reg

    def get_expiring_registrations(self, days_ahead: int = 30,
                                   as_of_date=None) -> list:
        """Return {registration, vehicle, days_remaining} for the latest
        COMPLETED registration per vehicle that expires within the window
        (or has already expired)."""
        from datetime import date as _date
        as_of_date = as_of_date or _date.today()
        results = []
        completed = (VehicleRegistration.query
                    .filter_by(status="COMPLETED")
                    .filter(VehicleRegistration.expiry_date.isnot(None))
                    .order_by(VehicleRegistration.expiry_date.asc())
                    .all())
        latest_by_vehicle = {}
        for reg in completed:
            latest_by_vehicle[reg.vehicle_id] = reg  # keep the latest seen
        for reg in latest_by_vehicle.values():
            days_remaining = (reg.expiry_date - as_of_date).days
            if days_remaining <= days_ahead:
                results.append({
                    "registration": reg, "vehicle": reg.vehicle,
                    "days_remaining": days_remaining})
        return results
