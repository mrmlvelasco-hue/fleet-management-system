"""Authority To Drive service: creation, submit/approve/reject/return/cancel
(shared base), plus the activate physical-lifecycle action specific to ATD."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.atd.models import AuthorityToDrive


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
    EDITABLE_STATUSES = ("DRAFT", "ACTIVE")
    EDIT_FIELDS = {
        "vehicle_id": ("Vehicle", "id"),
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

    def create(self, *, vehicle_id, driver_id, purpose, valid_from, valid_to,
               user, maintenance_order_id=None, odometer_out=None):
        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        atd = AuthorityToDrive(
            document_number=doc_number, vehicle_id=vehicle_id,
            driver_id=driver_id, purpose=purpose, valid_from=valid_from,
            valid_to=valid_to, maintenance_order_id=maintenance_order_id,
            odometer_out=odometer_out, status="DRAFT",
            requested_by=user.id if user else None)
        db.session.add(atd)
        db.session.commit()
        return atd

    def _validate_edit(self, record, fields):
        """The checks ATD's own PATCH endpoint used to make, here so they
        hold for every caller."""
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
