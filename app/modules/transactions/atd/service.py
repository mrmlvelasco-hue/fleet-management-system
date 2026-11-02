"""Authority To Drive service: creation, submit/approve/reject/return/cancel
(shared base), plus the activate physical-lifecycle action specific to ATD."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.atd.models import AuthorityToDrive

SPECIFIC_VEHICLE = "SPECIFIC_VEHICLE"
VEHICLE_TYPE = "VEHICLE_TYPE"
MOTORCYCLE_ONLY = "MOTORCYCLE_ONLY"
AUTHORIZATION_TYPES = {SPECIFIC_VEHICLE, VEHICLE_TYPE, MOTORCYCLE_ONLY}


class ATDValidationError(ValueError):
    """An ATD scope is missing or inconsistent."""


class InvalidATDStateError(Exception):
    pass


class ATDService(BaseTransactionService):
    model = AuthorityToDrive
    document_type_code = "ATD"
    reference_table = "authority_to_drives"
    # Edit policy (BaseTransactionService) -- replaces ATD's own
    # Draft/Returned-only update().
    update_permission = "atd.update"
    #: Active: the authority is in use -- still open, like an In-Progress MO.
    #: An ACTIVE permit closes on its own once Valid To passes; see
    #: editable_scope below.
    EDITABLE_STATUSES = ("DRAFT", "ACTIVE")
    EDIT_FIELDS = {
        "vehicle_id": ("Vehicle", "id"),
        "authorization_type": ("Authorization type", "text"),
        "vehicle_type_id": ("Vehicle type", "id"),
        "vehicle_category": ("Vehicle category", "text"),
        "driver_id": ("Driver", "id"),
        "purpose": ("Purpose", "text"),
        "valid_from": ("Valid from", "date"),
        "valid_to": ("Valid to", "date"),
        "odometer_out": ("Odometer out", "int"),
        "maintenance_order_id": ("Linked maintenance order", "id"),
    }
    date_fields = (("valid_from", "Valid From"),
                   ("valid_to", "Valid To"),
                   ("created_at", "Created Date"))

    def create(self, *, vehicle_id=None, authorization_type=SPECIFIC_VEHICLE,
               vehicle_type_id=None, vehicle_category=None, driver_id, purpose, valid_from, valid_to,
               user, maintenance_order_id=None, odometer_out=None):
        vehicle_category = (vehicle_category.strip().upper()
                            if vehicle_category else None)
        self._validate_scope(authorization_type, vehicle_id, vehicle_type_id,
                             vehicle_category)
        if not driver_id:
            raise ATDValidationError("driver_id is required.")
        # Odometer belongs to a particular vehicle, never to a type-wide or
        # motorcycle-only authorization.
        if authorization_type != SPECIFIC_VEHICLE:
            odometer_out = None
        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        atd = AuthorityToDrive(
            document_number=doc_number, vehicle_id=vehicle_id,
            authorization_type=authorization_type,
            vehicle_type_id=vehicle_type_id,
            vehicle_category=vehicle_category,
            driver_id=driver_id, purpose=purpose, valid_from=valid_from,
            valid_to=valid_to, maintenance_order_id=maintenance_order_id,
            odometer_out=odometer_out, status="DRAFT",
            requested_by=user.id if user else None)
        db.session.add(atd)
        db.session.commit()
        return atd

    @staticmethod
    def _validate_scope(authorization_type, vehicle_id, vehicle_type_id,
                        vehicle_category=None):
        from app.modules.master_data.vehicle.models import Vehicle
        from app.modules.master_data.reference.models import VehicleType
        if authorization_type not in AUTHORIZATION_TYPES:
            raise ATDValidationError("Select a valid authorization type.")
        if authorization_type == SPECIFIC_VEHICLE:
            if not vehicle_id:
                raise ATDValidationError(
                    "A vehicle is required for Specific Vehicle authorization.")
            vehicle = db.session.get(Vehicle, vehicle_id)
            if vehicle is None or not vehicle.is_active:
                raise ATDValidationError("The selected vehicle was not found.")
            if vehicle_type_id or vehicle_category:
                raise ATDValidationError(
                    "Specific Vehicle authorization cannot also select a vehicle type.")
        elif authorization_type == VEHICLE_TYPE:
            if vehicle_id:
                raise ATDValidationError(
                    "Choose either a vehicle type or a specific vehicle.")
            if vehicle_category:
                from app.modules.system_admin.services.lookup_service import (
                    LookupService)
                codes = {row.code for row in
                        LookupService().get_by_type_with_fallback(
                            "VEHICLE_CATEGORY")}
                if vehicle_category.strip().upper() not in codes:
                    raise ATDValidationError(
                        "Select an active Vehicle Category from Lookup Maintenance.")
                if vehicle_type_id:
                    raise ATDValidationError(
                        "Choose a vehicle category, not both a category and vehicle type.")
            elif vehicle_type_id:
                # Compatibility for ATDs submitted by clients from the
                # earlier v293 type-specific authorization form.
                vehicle_type = db.session.get(VehicleType, vehicle_type_id)
                if vehicle_type is None or not vehicle_type.is_active:
                    raise ATDValidationError("The selected vehicle type was not found.")
            else:
                raise ATDValidationError("A vehicle category is required.")
        elif vehicle_id or vehicle_type_id or vehicle_category:
            raise ATDValidationError(
                "Motorcycle Only authorization cannot be linked to a vehicle or type.")

    def _validate_edit(self, record, fields):
        super()._validate_edit(record, fields)
        from app.modules.transactions.base_service import DocumentFieldError
        merged = {"authorization_type": fields.get(
                      "authorization_type", record.authorization_type or SPECIFIC_VEHICLE),
                  "vehicle_id": fields.get("vehicle_id", record.vehicle_id),
                  "vehicle_type_id": fields.get("vehicle_type_id", record.vehicle_type_id),
                  "vehicle_category": fields.get("vehicle_category", record.vehicle_category)}
        try:
            self._validate_scope(**merged)
        except ATDValidationError as exc:
            raise DocumentFieldError(str(exc), "authorization_type") from exc
        if merged["authorization_type"] != SPECIFIC_VEHICLE and "odometer_out" not in fields:
            record.odometer_out = None

    @staticmethod
    def authorizes_vehicle(atd, vehicle, on_date=None):
        from datetime import date
        on_date = on_date or date.today()
        if (atd is None or vehicle is None or not vehicle.vehicle_type
                or not vehicle.vehicle_type.is_active or atd.status != "ACTIVE"
                or not atd.approval_instance
                or atd.approval_instance.status != "APPROVED"
                or not atd.valid_from or not atd.valid_to
                or not (atd.valid_from <= on_date <= atd.valid_to)
                or vehicle.status != "ACTIVE" or not vehicle.is_active):
            return False
        mode = atd.authorization_type or SPECIFIC_VEHICLE
        if mode == SPECIFIC_VEHICLE:
            return atd.vehicle_id == vehicle.id
        if mode == VEHICLE_TYPE:
            if atd.vehicle_id is not None:
                return False
            if atd.vehicle_category:
                from app.modules.system_admin.services.lookup_service import (
                    LookupService)
                codes = {row.code for row in
                        LookupService().get_by_type_with_fallback(
                            "VEHICLE_CATEGORY")}
                return bool(atd.vehicle_category.upper() in codes
                            and vehicle.vehicle_type.category
                            and vehicle.vehicle_type.category.upper()
                            == atd.vehicle_category.upper())
            return atd.vehicle_type_id == vehicle.vehicle_type_id
        if mode == MOTORCYCLE_ONLY:
            kind = vehicle.vehicle_type
            return bool(kind and (kind.category or "").upper() == "MOTORCYCLE")
        return False

    @staticmethod
    def is_expired(record) -> bool:
        """Has this authority lapsed?

        Either because the daily scan has already stamped it EXPIRED, or
        because its Valid To is in the past and nothing has run since
        (KI-10: no job flips the status in real time, so the date is the
        authority, not the stored word). Valid To is INCLUSIVE -- a
        permit valid "to the 14th" is live for the whole of the 14th.

        A DRAFT is excluded on purpose: it never authorised anything, so
        past dates on it are precisely what someone is editing to fix.
        """
        from datetime import date as _date
        if record is None:
            return False
        if record.status == "EXPIRED":
            return True
        return bool(record.status == "ACTIVE" and record.valid_to
                    and record.valid_to < _date.today())

    def editable_scope(self, record) -> str:
        """The shared rule, plus: a lapsed permit is finished.

        Extending Valid To on an expired slip would back-date authority
        for days nobody approved, and the printed copy the gate guard
        holds would no longer match the record. New dates need a new
        ATD (Michael, 2026-10-13).
        """
        if self.is_expired(record):
            return "NONE"
        return super().editable_scope(record)

    def update(self, record_id: int, *, user=None, **fields):
        """Same as the base, with the expired case explained rather than
        reported as a bare status conflict -- the person needs to know
        the way forward is a new request."""
        record = db.session.get(self.model, record_id)
        if self.is_expired(record):
            from app.modules.transactions.base_service import (
                DocumentStateError)
            raise DocumentStateError(
                "This Authority To Drive has expired and can no longer be "
                "edited. Raise a new ATD for the dates you need.")
        return super().update(record_id, user=user, **fields)

    def expire_overdue(self) -> int:
        """Stamp every lapsed ACTIVE permit EXPIRED; returns how many.

        The physical-lifecycle counterpart of the PM and registration
        due scans (`flask atd run-expiry-check`, or the Celery task).
        Idempotent -- a second run finds nothing, because only ACTIVE
        rows are considered.
        """
        from datetime import date as _date
        lapsed = (AuthorityToDrive.query
                  .filter(AuthorityToDrive.status == "ACTIVE",
                          AuthorityToDrive.valid_to.isnot(None),
                          AuthorityToDrive.valid_to < _date.today())
                  .all())
        for atd in lapsed:
            atd.status = "EXPIRED"
        if lapsed:
            db.session.commit()
        return len(lapsed)

    def _validate_edit(self, record, fields):
        """The checks ATD's own PATCH endpoint used to make, here so they
        hold for every caller."""
        if fields.get("vehicle_category"):
            fields["vehicle_category"] = fields["vehicle_category"].strip().upper()
        from app.modules.transactions.base_service import DocumentFieldError
        if "purpose" in fields and not (fields["purpose"] or "").strip():
            raise DocumentFieldError("purpose is required.", "purpose")
        start = fields.get("valid_from", record.valid_from)
        end = fields.get("valid_to", record.valid_to)
        if start and end and end < start:
            raise DocumentFieldError(
                "valid_to must be on or after valid_from.", "valid_to")

    def record_odometer_in(self, atd_id: int, odometer_in: int):
        """The gate guard's return checkpoint — recorded separately from
        creation since it isn't known until the vehicle actually comes
        back."""
        atd = db.session.get(AuthorityToDrive, atd_id)
        if not atd or atd.authorization_type != SPECIFIC_VEHICLE or not atd.vehicle_id:
            raise ATDValidationError(
                "Odometer In can only be recorded for a specific vehicle ATD.")
        atd.odometer_in = odometer_in
        db.session.commit()
        return atd

    def activate(self, atd_id: int):
        atd = db.session.get(AuthorityToDrive, atd_id)
        if not atd.approval_instance or atd.approval_instance.status != "APPROVED":
            raise InvalidATDStateError(
                "ATD must be APPROVED before it can be activated.")
        atd.status = "ACTIVE"
        db.session.commit()
        return atd
