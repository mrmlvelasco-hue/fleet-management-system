"""Vehicle Movement service: creation, submit/approve/reject/return/cancel
(shared base), plus start_transit/complete physical-lifecycle actions."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.vehicle_movement.models import VehicleMovement

VALID_MOVEMENT_TYPES = {"TRANSFER", "DISPATCH", "RETURN", "OTHER"}


class InvalidMovementTypeError(Exception):
    pass


class VehicleMovementService(BaseTransactionService):
    model = VehicleMovement
    document_type_code = "VM"
    reference_table = "vehicle_movements"

    def create(self, *, vehicle_id, movement_type, from_location,
               to_location, movement_date, user, remarks=None):
        if movement_type not in VALID_MOVEMENT_TYPES:
            raise InvalidMovementTypeError(
                f"'{movement_type}' is not a valid movement type. "
                f"Must be one of: {', '.join(sorted(VALID_MOVEMENT_TYPES))}.")

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        mv = VehicleMovement(
            document_number=doc_number, vehicle_id=vehicle_id,
            movement_type=movement_type, from_location=from_location,
            to_location=to_location, movement_date=movement_date,
            remarks=remarks, status="DRAFT",
            requested_by=user.id if user else None)
        db.session.add(mv)
        db.session.commit()
        return mv

    def start_transit(self, movement_id: int):
        mv = db.session.get(VehicleMovement, movement_id)
        mv.status = "IN_TRANSIT"
        db.session.commit()
        return mv

    def complete(self, movement_id: int):
        mv = db.session.get(VehicleMovement, movement_id)
        mv.status = "COMPLETED"
        db.session.commit()
        return mv
