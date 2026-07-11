"""Battery Transaction service: mount/dismount/dispose events that keep
the Battery master's status and vehicle link in sync."""
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

        txn = BatteryTransaction(
            document_number=doc_number, battery_id=battery_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date, remarks=remarks,
            status="COMPLETED", requested_by=user.id if user else None)
        db.session.add(txn)

        battery = db.session.get(Battery, battery_id)
        if action == "MOUNT":
            battery.status = "MOUNTED"
        elif action == "DISMOUNT":
            battery.status = "IN_STOCK"
        elif action == "DISPOSE":
            battery.status = "DISPOSED"
            battery.is_active = False

        db.session.commit()
        return txn
