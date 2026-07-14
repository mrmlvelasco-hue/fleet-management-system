from datetime import date, datetime

import pytest

from app.core.approval.engine import ApprovalEngine
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User, Role
from app.modules.transactions.maintenance_order.service import MaintenanceOrderService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-APCHAIN", name="Approval Chain Branch")
    vt = VehicleTypeService().create(code="LV-APCHAIN", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(code="APCHAIN-5K", name="5K PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=branch.id, conduction_number="APCHAIN-000")

    role1 = Role(name="Level1 Approver Role")
    role2 = Role(name="Level2 Approver Role")
    approver1 = User(username="apchain_l1", email="apchain_l1@x.com", password_hash="x")
    approver1.roles.append(role1)
    approver2 = User(username="apchain_l2", email="apchain_l2@x.com", password_hash="x")
    approver2.roles.append(role2)
    outsider = User(username="apchain_outsider", email="apchain_outsider@x.com",
                    password_hash="x")
    requester = User(username="apchain_requester", email="apchain_requester@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([role1, role2, approver1, approver2, outsider, requester])
    _db.session.commit()

    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=True, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    path = ApprovalPathService().create(name="Two Level Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role1.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": role2.id},
    ])
    ApprovalMatrixService().create(dt.id, path.id, min_amount=None, max_amount=None)

    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=requester)
    MaintenanceOrderService().submit(order.id, user=requester)

    return order, approver1, approver2, outsider, requester, role1, role2


def test_is_eligible_approver_true_for_current_level_role_holder(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    eligible = ApprovalEngine().is_eligible_approver(order.approval_instance, approver1)
    assert eligible is True


def test_is_eligible_approver_false_for_wrong_level_role_holder(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    eligible = ApprovalEngine().is_eligible_approver(order.approval_instance, approver2)
    assert eligible is False


def test_is_eligible_approver_false_for_outsider(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    eligible = ApprovalEngine().is_eligible_approver(order.approval_instance, outsider)
    assert eligible is False


def test_is_eligible_approver_does_not_raise(db, env):
    """This must be a safe, read-only check — never raises, even for
    someone completely ineligible."""
    order, approver1, approver2, outsider, requester, role1, role2 = env
    # Should not raise NotEligibleApproverError
    result = ApprovalEngine().is_eligible_approver(order.approval_instance, outsider)
    assert result in (True, False)


def test_approval_chain_shows_all_levels_with_correct_status(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    chain = ApprovalEngine().get_approval_chain(order.approval_instance)
    assert len(chain) == 2
    assert chain[0]["level_number"] == 1
    assert chain[0]["status"] == "CURRENT"
    assert chain[1]["level_number"] == 2
    assert chain[1]["status"] == "WAITING"


def test_approval_chain_reflects_progress_after_level_1_approved(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    MaintenanceOrderService().approve(order.id, user=approver1)

    chain = ApprovalEngine().get_approval_chain(order.approval_instance)
    assert chain[0]["status"] == "APPROVED"
    assert chain[0]["acted_by_name"] is not None
    assert chain[1]["status"] == "CURRENT"


def test_approval_chain_shows_approver_label(db, env):
    order, approver1, approver2, outsider, requester, role1, role2 = env
    chain = ApprovalEngine().get_approval_chain(order.approval_instance)
    assert "Level1 Approver Role" in chain[0]["approver_label"]
