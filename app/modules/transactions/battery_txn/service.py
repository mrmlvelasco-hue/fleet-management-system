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

VALID_ACTIONS = {"MOUNT", "DISMOUNT", "DISPOSE", "TEST"}
VALID_TEST_RESULTS = {"GOOD", "WEAK", "REPLACE"}


class InvalidBatteryActionError(Exception):
    pass


from app.modules.transactions.tire_txn.service import _dec, _int  # noqa: E402


class BatteryTransactionService(BaseTransactionService):
    model = BatteryTransaction
    document_type_code = "BAT"
    reference_table = "battery_transactions"
    # Edit policy (BaseTransactionService).
    # battery.* like the rest of the React-facing battery transaction API
    # (Flask screens and the menu use batterytxn.* -- flagged, not changed).
    update_permission = "battery.update"
    EDIT_FIELDS = {
        "battery_id": ("Battery", "id"),
        "action": ("Action", "text"),
        "vehicle_id": ("Vehicle", "id"),
        "transaction_date": ("Transaction date", "date"),
        "odometer_at_service": ("Odometer", "int"),
        "voltage": ("Voltage", "text"),
        "test_result": ("Test result", "text"),
        "cost": ("Cost", "money"),
        "remarks": ("Remarks", "text"),
    }
    date_fields = (("transaction_date", "Transaction Date"),
                   ("created_at", "Created Date"))

    def _document_requires_approval(self) -> bool:
        from app.modules.document_config.repository import DocumentTypeRepository
        dt = DocumentTypeRepository().get_by_code(self.document_type_code)
        return bool(dt and dt.requires_approval)

    def _apply_physical_effect(self, txn) -> None:
        """MOUNT puts the battery ON the vehicle; DISMOUNT / DISPOSE take
        it off; TEST only records the reading. (Before 2026-10-15 a
        MOUNT changed the status alone.)"""
        battery = db.session.get(Battery, txn.battery_id)
        if txn.action == "MOUNT":
            battery.status = "MOUNTED"
            battery.vehicle_id = txn.vehicle_id
            battery.mounted_date = txn.transaction_date
            battery.mounted_odometer = txn.odometer_at_service
        elif txn.action in ("DISMOUNT", "DISPOSE"):
            battery.status = ("IN_STOCK" if txn.action == "DISMOUNT"
                              else "DISPOSED")
            battery.vehicle_id = None
            battery.mounted_date = None
            battery.mounted_odometer = None
            if txn.action == "DISPOSE":
                battery.is_active = False
        if txn.voltage is not None:
            battery.last_voltage = txn.voltage
            battery.last_test_date = txn.transaction_date

    def _validate_edit(self, record, fields):
        """Same action list as create()."""
        if "action" in fields and fields["action"] not in VALID_ACTIONS:
            from app.modules.transactions.base_service import (
                DocumentFieldError)
            raise DocumentFieldError(
                f"'{fields['action']}' is not a valid battery action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.",
                "action")

    def create(self, *, battery_id, action, transaction_date, user,
               vehicle_id=None, remarks=None, odometer_at_service=None,
               voltage=None, test_result=None, cost=None, source="MANUAL",
               maintenance_order_id=None, force_completed=False,
               commit=True):
        """force_completed: see TireTransactionService.create."""
        if action not in VALID_ACTIONS:
            raise InvalidBatteryActionError(
                f"'{action}' is not a valid battery action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.")
        if test_result and test_result not in VALID_TEST_RESULTS:
            raise InvalidBatteryActionError(
                f"'{test_result}' is not a valid test result. Must be one "
                f"of: {', '.join(sorted(VALID_TEST_RESULTS))}.")

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        requires_approval = (not force_completed
                             and self._document_requires_approval())

        txn = BatteryTransaction(
            document_number=doc_number, battery_id=battery_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date, remarks=remarks,
            odometer_at_service=_int(odometer_at_service),
            voltage=_dec(voltage), test_result=test_result or None,
            cost=_dec(cost), source=source or "MANUAL",
            maintenance_order_id=maintenance_order_id,
            status="DRAFT" if requires_approval else "COMPLETED",
            requested_by=user.id if user else None)
        db.session.add(txn)
        db.session.flush()

        if not requires_approval:
            self._apply_physical_effect(txn)

        if commit:
            db.session.commit()
        return txn

    def approve(self, record_id: int, user, remarks=None):
        record = super().approve(record_id, user, remarks)
        if record.approval_instance and record.approval_instance.status == "APPROVED":
            self._apply_physical_effect(record)
            record.status = "COMPLETED"
            db.session.commit()
        return record
