"""Vehicle Movement service: creation, submit/approve/reject/return/cancel
(shared base), plus start_transit/complete physical-lifecycle actions.

movement_type is validated against the MOVEMENT_TYPE Lookup (System
Administration → Lookups) rather than a hardcoded set, so an admin can add
new movement types without a code change — this field is purely descriptive
and doesn't drive any branching logic."""
from app.extensions import db
from app.core.numbering.numbering_service import AutoNumberingService
from app.modules.transactions.base_service import BaseTransactionService
from app.modules.transactions.vehicle_movement.models import VehicleMovement


class InvalidMovementTypeError(Exception):
    pass


class VehicleMovementService(BaseTransactionService):
    model = VehicleMovement
    document_type_code = "VM"
    reference_table = "vehicle_movements"

    def create(self, *, vehicle_id, movement_type, from_location,
               to_location, movement_date, user, remarks=None):
        from app.modules.system_admin.services.lookup_service import (
            LookupService, registry as lookup_registry)
        valid_types = {i.code for i in
                      LookupService().get_by_type("MOVEMENT_TYPE")}
        if not valid_types:
            # Fresh install, `flask seed all` not yet run: fall back to the
            # code-registered defaults so this doesn't hard-depend on seeding.
            valid_types = {d.code for d in lookup_registry.definitions
                          if d.lookup_type == "MOVEMENT_TYPE"}
        if movement_type not in valid_types:
            raise InvalidMovementTypeError(
                f"'{movement_type}' is not a valid movement type. "
                f"Must be one of: {', '.join(sorted(valid_types))}.")

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
