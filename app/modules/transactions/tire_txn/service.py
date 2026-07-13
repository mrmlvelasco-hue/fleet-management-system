"""Tire Transaction service: mount/dismount/retread/dispose events that
keep the Tire master's status and vehicle link in sync.

By default (Document Type "Requires Approval" = No), a transaction
completes immediately on creation — routine, low-risk parts-tracking
actions don't need sign-off. If an admin flips "Requires Approval" on for
the TIR document type, transactions instead go through the standard
Draft → Submit → Approve/Reject/Return workflow (inherited from
BaseTransactionService, same as every other module), and the physical
status change on the Tire master is only applied once the transaction is
actually approved — not at draft creation, since the action hasn't really
taken effect until sign-off.
"""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.tire_txn.models import TireTransaction
from app.modules.master_data.tire.models import Tire

VALID_ACTIONS = {"MOUNT", "DISMOUNT", "RETREAD", "DISPOSE"}


class InvalidTireActionError(Exception):
    pass


class TireTransactionService(BaseTransactionService):
    model = TireTransaction
    document_type_code = "TIR"
    reference_table = "tire_transactions"

    def _document_requires_approval(self) -> bool:
        from app.modules.document_config.repository import DocumentTypeRepository
        dt = DocumentTypeRepository().get_by_code(self.document_type_code)
        return bool(dt and dt.requires_approval)

    def _apply_physical_effect(self, txn) -> None:
        tire = db.session.get(Tire, txn.tire_id)
        if txn.action == "MOUNT":
            tire.status = "MOUNTED"
        elif txn.action == "DISMOUNT":
            tire.status = "IN_STOCK"
        elif txn.action == "RETREAD":
            tire.status = "RETREADED"
        elif txn.action == "DISPOSE":
            tire.status = "DISPOSED"
            tire.is_active = False

    def create(self, *, tire_id, action, transaction_date, user,
               vehicle_id=None, odometer_at_service=None, remarks=None):
        if action not in VALID_ACTIONS:
            raise InvalidTireActionError(
                f"'{action}' is not a valid tire action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.")

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        requires_approval = self._document_requires_approval()

        txn = TireTransaction(
            document_number=doc_number, tire_id=tire_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date,
            odometer_at_service=odometer_at_service, remarks=remarks,
            status="DRAFT" if requires_approval else "COMPLETED",
            requested_by=user.id if user else None)
        db.session.add(txn)
        db.session.flush()

        if not requires_approval:
            self._apply_physical_effect(txn)

        db.session.commit()
        return txn

    def approve(self, record_id: int, user, remarks=None):
        record = super().approve(record_id, user, remarks)
        if record.approval_instance and record.approval_instance.status == "APPROVED":
            self._apply_physical_effect(record)
            record.status = "COMPLETED"
            db.session.commit()
        return record
