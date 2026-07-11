"""Tire Transaction service: mount/dismount/retread/dispose events that
keep the Tire master's status and vehicle link in sync."""
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

        txn = TireTransaction(
            document_number=doc_number, tire_id=tire_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date,
            odometer_at_service=odometer_at_service, remarks=remarks,
            status="COMPLETED", requested_by=user.id if user else None)
        db.session.add(txn)

        tire = db.session.get(Tire, tire_id)
        if action == "MOUNT":
            tire.status = "MOUNTED"
        elif action == "DISMOUNT":
            tire.status = "IN_STOCK"
        elif action == "RETREAD":
            tire.status = "RETREADED"
        elif action == "DISPOSE":
            tire.status = "DISPOSED"
            tire.is_active = False

        db.session.commit()
        return txn
