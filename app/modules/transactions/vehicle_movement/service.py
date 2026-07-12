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
from app.modules.master_data.vehicle.models import Vehicle


class InvalidMovementTypeError(Exception):
    pass


class VehicleMovementService(BaseTransactionService):
    model = VehicleMovement
    document_type_code = "VM"
    reference_table = "vehicle_movements"

    def create(self, *, vehicle_id, movement_type, from_location,
               to_location, movement_date, user, remarks=None,
               driver_id=None, employee_responsible=None, purpose=None,
               movement_start_datetime=None):
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

        # Default the driver from the vehicle's own assignment unless the
        # caller explicitly specifies one for this particular movement.
        if driver_id is None:
            vehicle = db.session.get(Vehicle, vehicle_id)
            driver_id = vehicle.assigned_driver_id if vehicle else None

        numbering = AutoNumberingService()
        try:
            doc_number = numbering.generate(self.document_type_code)
        except Exception:
            doc_number = None

        mv = VehicleMovement(
            document_number=doc_number, vehicle_id=vehicle_id,
            driver_id=driver_id, employee_responsible=employee_responsible,
            purpose=purpose, movement_type=movement_type,
            from_location=from_location, to_location=to_location,
            movement_date=movement_date,
            movement_start_datetime=movement_start_datetime,
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

    def complete(self, movement_id: int, movement_end_datetime=None):
        mv = db.session.get(VehicleMovement, movement_id)
        mv.status = "COMPLETED"
        if movement_end_datetime is not None:
            mv.movement_end_datetime = movement_end_datetime
        db.session.commit()
        return mv
