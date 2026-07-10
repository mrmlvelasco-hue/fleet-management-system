from datetime import date

import pytest

from app.modules.transactions.vehicle_movement.service import (
    VehicleMovementService, InvalidMovementTypeError)
from app.modules.transactions.vehicle_movement.models import VehicleMovement
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-VM", name="VM Branch")
    vt = VehicleTypeService().create(code="LV-VM", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Dmax", year=2024,
        branch_id=branch.id, conduction_number="VM-000")
    user = User(username="vm_requester", email="vm@x.com", password_hash="x")
    db.session.add(user)
    db.session.commit()

    dt = DocumentTypeService().create(code="VM", name="Vehicle Movement",
                                      requires_approval=False,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="VM",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return vehicle, user


def test_create_movement(db, env):
    vehicle, user = env
    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="TRANSFER",
                    from_location="HQ", to_location="Branch 2",
                    movement_date=date(2026, 7, 15), user=user)
    assert mv.document_number.startswith("VM-")
    assert mv.status == "DRAFT"


def test_invalid_movement_type_rejected(db, env):
    vehicle, user = env
    with pytest.raises(InvalidMovementTypeError):
        VehicleMovementService().create(
            vehicle_id=vehicle.id, movement_type="TELEPORT",
            from_location="HQ", to_location="Mars",
            movement_date=date(2026, 7, 15), user=user)


def test_submit_auto_approves_and_start_complete_transit(db, env):
    vehicle, user = env
    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="TRANSFER",
                    from_location="HQ", to_location="Branch 2",
                    movement_date=date(2026, 7, 15), user=user)
    svc.submit(mv.id, user=user)
    svc.start_transit(mv.id)
    assert db.session.get(VehicleMovement, mv.id).status == "IN_TRANSIT"
    svc.complete(mv.id)
    assert db.session.get(VehicleMovement, mv.id).status == "COMPLETED"


def test_cancel_movement(db, env):
    vehicle, user = env
    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="DISPATCH",
                    from_location="HQ", to_location="Site A",
                    movement_date=date(2026, 7, 15), user=user)
    svc.cancel(mv.id, user=user)
    assert db.session.get(VehicleMovement, mv.id).status == "CANCELLED"
