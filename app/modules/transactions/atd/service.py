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
