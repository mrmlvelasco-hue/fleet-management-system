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


# ── KI-04: start_transit/complete must gate on the SAME document type
# the movement was actually submitted under ──────────────────────────
#
# document_type_code on this service is "VM" -- that's what create()'s
# numbering, submit()'s ApprovalEngine call, and the worklist all use.
# But start_transit()/complete() checked DocumentType code
# "VEHICLEMOVEMENT" instead, a code nothing in the real system ever
# creates (an admin configuring this document type in System
# Administration sets it up as "VM", because that's the code every
# other part of the module uses). So requires_approval always resolved
# False there, regardless of how "VM" was actually configured -- a
# movement could be started and completed with no approval at all, even
# configured and never submitted.
#
# The `env` fixture above seeds "VM" with requires_approval=False,
# which is why the existing tests above never caught this -- none of
# them exercise the configured-to-require-approval case.

def test_start_transit_blocked_when_vm_requires_approval_and_never_submitted(
        db, env):
    from app.modules.transactions.vehicle_movement.service import (
        InvalidMovementStateError)
    from app.modules.document_config.models import DocumentType
    vehicle, user = env
    DocumentType.query.filter_by(code="VM").first().requires_approval = True
    db.session.commit()

    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="TRANSFER",
                    from_location="HQ", to_location="Branch 2",
                    movement_date=date(2026, 7, 15), user=user)
    # Never submitted -- mv.approval_instance is None.

    with pytest.raises(InvalidMovementStateError):
        svc.start_transit(mv.id)
    assert db.session.get(VehicleMovement, mv.id).status == "DRAFT"


def test_complete_blocked_when_vm_requires_approval_and_never_submitted(
        db, env):
    from app.modules.transactions.vehicle_movement.service import (
        InvalidMovementStateError)
    from app.modules.document_config.models import DocumentType
    vehicle, user = env
    DocumentType.query.filter_by(code="VM").first().requires_approval = True
    db.session.commit()

    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="TRANSFER",
                    from_location="HQ", to_location="Branch 2",
                    movement_date=date(2026, 7, 15), user=user)

    with pytest.raises(InvalidMovementStateError):
        svc.complete(mv.id)
    assert db.session.get(VehicleMovement, mv.id).status == "DRAFT"


def test_start_transit_allowed_once_the_vm_submission_is_approved(db, env):
    from app.modules.document_config.models import DocumentType
    from app.modules.approval_config.service import (
        ApprovalPathService, ApprovalMatrixService)
    from app.modules.user_management.models import Role
    vehicle, user = env
    dt = DocumentType.query.filter_by(code="VM").first()
    dt.requires_approval = True
    approver_role = Role(name="VM Approver")
    approver = User(username="vm_approver", email="vma@x.com",
                    password_hash="x")
    approver.roles.append(approver_role)
    db.session.add_all([approver_role, approver])
    db.session.commit()
    path = ApprovalPathService().create(name="VM Path", levels=[
        {"level_number": 1, "approver_type": "ROLE",
         "role_id": approver_role.id}])
    ApprovalMatrixService().create(dt.id, path.id)

    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="TRANSFER",
                    from_location="HQ", to_location="Branch 2",
                    movement_date=date(2026, 7, 15), user=user)
    svc.submit(mv.id, user=user)
    svc.approve(mv.id, user=approver)

    svc.start_transit(mv.id)
    assert db.session.get(VehicleMovement, mv.id).status == "IN_TRANSIT"


def test_cancel_movement(db, env):
    vehicle, user = env
    svc = VehicleMovementService()
    mv = svc.create(vehicle_id=vehicle.id, movement_type="DISPATCH",
                    from_location="HQ", to_location="Site A",
                    movement_date=date(2026, 7, 15), user=user)
    svc.cancel(mv.id, user=user)
    assert db.session.get(VehicleMovement, mv.id).status == "CANCELLED"
