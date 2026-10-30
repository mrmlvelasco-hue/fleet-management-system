"""Battery Transaction service: mount/dismount/dispose events that keep
the Battery master's status and vehicle link in sync.

Same approval-gating behavior as TireTransactionService — see that
module's docstring for the full rationale.
"""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.battery_txn.models import (
    BatteryTransaction, BatteryTransactionScopeItem)
from app.modules.transactions import equipment_replace as rep
from app.modules.master_data.battery.models import Battery

# INSPECT, CHARGE and TEST only record the reading (mockup 2026-10-15).
VALID_ACTIONS = {"MOUNT", "DISMOUNT", "DISPOSE", "TEST", "REPLACE",
                 "INSPECT", "CHARGE"}
VALID_TEST_RESULTS = {"GOOD", "WEAK", "REPLACE"}


class InvalidBatteryActionError(Exception):
    def __init__(self, message, field="action"):
        super().__init__(message)
        self.field = field


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
        if txn.action == "REPLACE":
            self._apply_replace(txn)
            return
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

    def _apply_replace(self, txn):
        """Old battery back to stock; the new one (registered now if the
        workshop supplied it) on the vehicle."""
        if txn.removed_battery_id:
            old = db.session.get(Battery, txn.removed_battery_id)
            old.status = "IN_STOCK"
            old.vehicle_id = None
            old.mounted_date = None
            old.mounted_odometer = None
        if txn.battery_id is None:
            vehicle = txn.vehicle
            from app.core.company_resolution import effective_company_id
            new = Battery(serial_number=txn.new_serial
                          or rep.auto_serial("BAT", txn),
                          brand=txn.new_brand, model=txn.new_model,
                          warranty_months=txn.new_warranty_months,
                          vendor_id=txn.vendor_id,
                          branch_id=vehicle.branch_id if vehicle else None,
                          company_id=effective_company_id(vehicle)
                          if vehicle else None,
                          purchase_date=txn.transaction_date,
                          purchase_cost=txn.cost, status="IN_STOCK")
            db.session.add(new)
            db.session.flush()
            txn.battery_id = new.id
        new = db.session.get(Battery, txn.battery_id)
        new.status = "MOUNTED"
        new.vehicle_id = txn.vehicle_id
        new.mounted_date = txn.transaction_date
        new.mounted_odometer = txn.odometer_at_service
        if txn.voltage is not None:
            new.last_voltage = txn.voltage
            new.last_test_date = txn.transaction_date

    def _validate_edit(self, record, fields):
        """Same action list as create()."""
        if "action" in fields and fields["action"] not in VALID_ACTIONS:
            from app.modules.transactions.base_service import (
                DocumentFieldError)
            raise DocumentFieldError(
                f"'{fields['action']}' is not a valid battery action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.",
                "action")

    def create(self, *, action, transaction_date, user, battery_id=None,
               vehicle_id=None, remarks=None, odometer_at_service=None,
               voltage=None, test_result=None, cost=None, source="MANUAL",
               maintenance_order_id=None, force_completed=False,
               commit=True, removed_battery_id=None, removal_reason=None,
               removed_to=None, install_source=None, new_serial=None,
               new_brand=None, new_model=None, new_warranty_months=None,
               vendor_id=None, technician=None, labor_cost=None,
               scope_items=None):
        """force_completed: see TireTransactionService.create. REPLACE is
        one document for both sides of the swap (equipment_replace)."""
        Err = InvalidBatteryActionError
        if action not in VALID_ACTIONS:
            raise Err(
                f"'{action}' is not a valid battery action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.")
        install_source = (rep.clean(install_source) or "").upper() or None
        removed_to = (rep.clean(removed_to) or "").upper() or None
        new_serial = rep.clean(new_serial, 60)
        if action == "REPLACE":
            rep.check_replace(
                kind="BATTERY", vehicle_id=vehicle_id, wheel_position=None,
                install_source=install_source, item_id=battery_id,
                new_serial=new_serial, new_brand=rep.clean(new_brand),
                new_size=None, removed_to=removed_to, model=Battery,
                err=Err)
            if install_source == "VENDOR":
                battery_id = None
            if not removed_battery_id:
                on = Battery.query.filter_by(vehicle_id=vehicle_id,
                                             status="MOUNTED").first()
                removed_battery_id = on.id if on else None
        elif not battery_id:
            raise Err("Choose the battery.", "battery_id")
        if source == "MANUAL":
            rep.check_linked_mo(maintenance_order_id, vehicle_id, Err)
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
            removed_battery_id=removed_battery_id,
            removal_reason=rep.clean(removal_reason, 40),
            removed_to=removed_to, install_source=install_source,
            new_serial=new_serial, new_brand=rep.clean(new_brand, 80),
            new_model=rep.clean(new_model, 80),
            new_warranty_months=_int(new_warranty_months),
            vendor_id=vendor_id, technician=rep.clean(technician, 120),
            labor_cost=_dec(labor_cost),
            status="DRAFT" if requires_approval else "COMPLETED",
            requested_by=user.id if user else None)
        db.session.add(txn)
        for row in rep.scope_rows(scope_items):
            txn.scope_items.append(BatteryTransactionScopeItem(**row))
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
