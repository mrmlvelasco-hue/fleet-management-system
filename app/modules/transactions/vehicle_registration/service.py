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


class RegistrationDateOrderError(Exception):
    pass


class DuplicateORNumberError(Exception):
    pass


class DuplicateCRNumberError(Exception):
    pass


class VehicleRegistrationService(BaseTransactionService):
    model = VehicleRegistration
    document_type_code = "VR"
    reference_table = "vehicle_registrations"

    def get_finance_params(self) -> dict:
        """VAT rate and % assured value for registration computations.

        These are read from System Parameters (FINANCE group) rather than
        hardcoded, matching the legacy VEMS 'Other Setting' config (VAT
        12%, % Assured Value 10). Admins change them under System
        Administration → System Parameters with no code change, satisfying
        the 'no values shall be hardcoded' requirement.
        """
        from decimal import Decimal
        from app.modules.system_admin.services.system_parameter_service import (
            SystemParameterService)
        svc = SystemParameterService()
        return {
            "vat_rate": svc.get("VAT_RATE", Decimal("12")),
            "assured_value_pct": svc.get("ASSURED_VALUE_PCT", Decimal("10")),
        }

    def compute_vat(self, base_amount) -> "Decimal":
        """Compute VAT on a base amount using the configured VAT rate."""
        from decimal import Decimal, ROUND_HALF_UP
        if base_amount is None:
            return Decimal("0.00")
        rate = self.get_finance_params()["vat_rate"]
        vat = (Decimal(str(base_amount)) * Decimal(str(rate)) / Decimal("100"))
        return vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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

        # Rule: a COMPLETED record can never have its registration date
        # after its own expiry date — that would mean the certificate
        # expired before it was even issued, always a data-entry error.
        if (reg.registration_date and reg.expiry_date
                and reg.registration_date > reg.expiry_date):
            raise RegistrationDateOrderError(
                f"Registration date ({reg.registration_date}) cannot be "
                f"after the expiry date ({reg.expiry_date}).")

        # Rule: OR/CR numbers must be unique across all registrations —
        # each is a real, physically-issued government document number,
        # so a duplicate almost always means the same document was
        # entered twice or copy-pasted from another vehicle's record by
        # mistake.
        if or_number:
            dup = (VehicleRegistration.query
                  .filter(VehicleRegistration.or_number == or_number,
                         VehicleRegistration.id != registration_id)
                  .first())
            if dup is not None:
                raise DuplicateORNumberError(
                    f"OR Number '{or_number}' is already used by "
                    f"registration {dup.document_number or dup.id} "
                    f"(vehicle: {dup.vehicle.plate_number or dup.vehicle.conduction_number if dup.vehicle else '—'}).")
        if cr_number:
            dup = (VehicleRegistration.query
                  .filter(VehicleRegistration.cr_number == cr_number,
                         VehicleRegistration.id != registration_id)
                  .first())
            if dup is not None:
                raise DuplicateCRNumberError(
                    f"CR Number '{cr_number}' is already used by "
                    f"registration {dup.document_number or dup.id} "
                    f"(vehicle: {dup.vehicle.plate_number or dup.vehicle.conduction_number if dup.vehicle else '—'}).")

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
