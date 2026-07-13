"""Battery Transaction service: mount/dismount/dispose events that keep
the Battery master's status and vehicle link in sync.

Same approval-gating behavior as TireTransactionService — see that
module's docstring for the full rationale.
"""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.battery_txn.models import BatteryTransaction
from app.modules.master_data.battery.models import Battery

VALID_ACTIONS = {"MOUNT", "DISMOUNT", "DISPOSE"}


class InvalidBatteryActionError(Exception):
    pass


class BatteryTransactionService(BaseTransactionService):
    model = BatteryTransaction
    document_type_code = "BAT"
    reference_table = "battery_transactions"

    def _document_requires_approval(self) -> bool:
        from app.modules.document_config.repository import DocumentTypeRepository
        dt = DocumentTypeRepository().get_by_code(self.document_type_code)
        return bool(dt and dt.requires_approval)

    def _apply_physical_effect(self, txn) -> None:
        battery = db.session.get(Battery, txn.battery_id)
        if txn.action == "MOUNT":
            battery.status = "MOUNTED"
        elif txn.action == "DISMOUNT":
            battery.status = "IN_STOCK"
        elif txn.action == "DISPOSE":
            battery.status = "DISPOSED"
            battery.is_active = False

    def create(self, *, battery_id, action, transaction_date, user,
               vehicle_id=None, remarks=None):
        if action not in VALID_ACTIONS:
            raise InvalidBatteryActionError(
                f"'{action}' is not a valid battery action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.")

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        requires_approval = self._document_requires_approval()

        txn = BatteryTransaction(
            document_number=doc_number, battery_id=battery_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date, remarks=remarks,
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
