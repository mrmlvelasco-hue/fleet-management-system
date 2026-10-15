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

VALID_ACTIONS = {"MOUNT", "DISMOUNT", "RETREAD", "DISPOSE",
                 "ROTATE", "INSPECT", "REPAIR"}
# Actions that leave the tire where it is and only record a reading.
READING_ACTIONS = {"INSPECT", "REPAIR"}
# Actions that need to know which vehicle the tire is on. MOUNT is NOT
# here: the existing tire screen and API have always accepted a mount with
# no vehicle chosen, and rejecting it now would break them (backward
# compatibility). The MO flow always supplies the vehicle.
VEHICLE_ACTIONS = {"ROTATE"}


class InvalidTireActionError(Exception):
    pass


def _dec(v):
    """Decimal or None; blanks and junk become None rather than 0."""
    from decimal import Decimal, InvalidOperation
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


class TireTransactionService(BaseTransactionService):
    model = TireTransaction
    document_type_code = "TIR"
    reference_table = "tire_transactions"
    # Edit policy (BaseTransactionService).
    # tire.* like the rest of the React-facing tire transaction API
    # (Flask screens and the menu use tiretxn.* -- flagged, not changed).
    update_permission = "tire.update"
    EDIT_FIELDS = {
        "tire_id": ("Tire", "id"),
        "action": ("Action", "text"),
        "vehicle_id": ("Vehicle", "id"),
        "transaction_date": ("Transaction date", "date"),
        "odometer_at_service": ("Odometer", "int"),
        "wheel_position": ("Wheel position", "text"),
        "tread_depth_mm": ("Tread depth (mm)", "text"),
        "pressure_psi": ("Pressure (psi)", "int"),
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
        """Bring the Tire master in line with what physically happened.

        MOUNT puts the tire ON the vehicle (vehicle + wheel); DISMOUNT,
        RETREAD and DISPOSE take it off. ROTATE moves it to another
        wheel. INSPECT / REPAIR only record the reading. Before
        2026-10-15 a MOUNT changed the status alone, so the master never
        said which vehicle or wheel the tire was on.
        """
        tire = db.session.get(Tire, txn.tire_id)
        if txn.action == "MOUNT":
            tire.status = "MOUNTED"
            tire.vehicle_id = txn.vehicle_id
            tire.wheel_position = txn.wheel_position
            tire.mounted_date = txn.transaction_date
            tire.mounted_odometer = txn.odometer_at_service
        elif txn.action in ("DISMOUNT", "RETREAD", "DISPOSE"):
            tire.status = {"DISMOUNT": "IN_STOCK", "RETREAD": "RETREADED",
                           "DISPOSE": "DISPOSED"}[txn.action]
            tire.vehicle_id = None
            tire.wheel_position = None
            tire.mounted_date = None
            tire.mounted_odometer = None
            if txn.action == "DISPOSE":
                tire.is_active = False
        elif txn.action == "ROTATE":
            tire.wheel_position = txn.wheel_position
        if txn.tread_depth_mm is not None:
            tire.current_tread_mm = txn.tread_depth_mm
            tire.last_inspection_date = txn.transaction_date

    def _validate_edit(self, record, fields):
        """Same action list as create()."""
        if "action" in fields and fields["action"] not in VALID_ACTIONS:
            from app.modules.transactions.base_service import (
                DocumentFieldError)
            raise DocumentFieldError(
                f"'{fields['action']}' is not a valid tire action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.",
                "action")

    def create(self, *, tire_id, action, transaction_date, user,
               vehicle_id=None, odometer_at_service=None, remarks=None,
               wheel_position=None, tread_depth_mm=None, pressure_psi=None,
               cost=None, source="MANUAL", maintenance_order_id=None,
               force_completed=False, commit=True):
        """force_completed: post as COMPLETED regardless of the TIR
        document type's approval setting. Used only for transactions an
        approved Maintenance Order generates on completion -- the MO's
        approval already covers the work (client decision 2026-10-15)."""
        if action not in VALID_ACTIONS:
            raise InvalidTireActionError(
                f"'{action}' is not a valid tire action. "
                f"Must be one of: {', '.join(sorted(VALID_ACTIONS))}.")
        if action in VEHICLE_ACTIONS and not vehicle_id:
            raise InvalidTireActionError(
                f"A {action.lower()} needs the vehicle the tire is on.")

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        requires_approval = (not force_completed
                             and self._document_requires_approval())

        txn = TireTransaction(
            document_number=doc_number, tire_id=tire_id,
            vehicle_id=vehicle_id, action=action,
            transaction_date=transaction_date,
            odometer_at_service=odometer_at_service, remarks=remarks,
            wheel_position=wheel_position,
            tread_depth_mm=_dec(tread_depth_mm),
            pressure_psi=_int(pressure_psi), cost=_dec(cost),
            source=source or "MANUAL",
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
