from datetime import date
from decimal import Decimal

import pytest

from app.modules.transactions.purchase_request.service import (
    PurchaseRequestService, LineManagementError)
from app.modules.transactions.purchase_request.models import PurchaseRequest
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.user_management.models import User, Role


@pytest.fixture()
def env(db):
    small_role = Role(name="Small Approver")
    large_role = Role(name="Large Approver")
    small_approver = User(username="small_appr", email="sa@x.com",
                          password_hash="x")
    small_approver.roles.append(small_role)
    large_approver = User(username="large_appr", email="la@x.com",
                         password_hash="x")
    large_approver.roles.append(large_role)
    requester = User(username="pr_requester", email="pr@x.com",
                     password_hash="x")
    db.session.add_all([small_role, large_role, small_approver,
                        large_approver, requester])
    db.session.commit()

    dt = DocumentTypeService().create(code="PR", name="Purchase Request",
                                      requires_approval=True,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="PR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    small_path = ApprovalPathService().create(name="Small PR Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": small_role.id}])
    large_path = ApprovalPathService().create(name="Large PR Path", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": large_role.id}])
    ApprovalMatrixService().create(dt.id, small_path.id, min_amount=0,
                                   max_amount=10000)
    ApprovalMatrixService().create(dt.id, large_path.id,
                                   min_amount=Decimal("10000.01"),
                                   max_amount=None)
    return small_approver, large_approver, requester


def test_create_with_lines_computes_amount(db, env):
    small_approver, large_approver, requester = env
    svc = PurchaseRequestService()
    pr = svc.create(description="Office supplies", user=requester, lines=[
        {"item_description": "Paper", "quantity": 10, "unit_cost": 50},
        {"item_description": "Pens", "quantity": 20, "unit_cost": 15},
    ])
    assert pr.document_number.startswith("PR-")
    assert pr.amount == 800  # 10*50 + 20*15


def test_add_line_recomputes_amount(db, env):
    small_approver, large_approver, requester = env
    svc = PurchaseRequestService()
    pr = svc.create(description="Test", user=requester, lines=[
        {"item_description": "Item A", "quantity": 1, "unit_cost": 100}])
    svc.add_line(pr.id, item_description="Item B", quantity=2, unit_cost=50)
    refreshed = db.session.get(PurchaseRequest, pr.id)
    assert refreshed.amount == 200  # 100 + 2*50


def test_cannot_modify_lines_after_submit(db, env):
    small_approver, large_approver, requester = env
    svc = PurchaseRequestService()
    pr = svc.create(description="Test", user=requester, lines=[
        {"item_description": "Item A", "quantity": 1, "unit_cost": 100}])
    svc.submit(pr.id, user=requester)
    with pytest.raises(LineManagementError):
        svc.add_line(pr.id, item_description="Item B", quantity=1, unit_cost=1)


def test_small_amount_routes_to_small_approver(db, env):
    small_approver, large_approver, requester = env
    svc = PurchaseRequestService()
    pr = svc.create(description="Small purchase", user=requester, lines=[
        {"item_description": "Item", "quantity": 1, "unit_cost": 5000}])
    svc.submit(pr.id, user=requester)
    refreshed = db.session.get(PurchaseRequest, pr.id)
    # small approver should be eligible; large should not
    svc.approve(pr.id, user=small_approver)
    assert refreshed.approval_instance.status == "APPROVED"


def test_large_amount_routes_to_large_approver(db, env):
    small_approver, large_approver, requester = env
    from app.core.approval.engine import NotEligibleApproverError
    svc = PurchaseRequestService()
    pr = svc.create(description="Big purchase", user=requester, lines=[
        {"item_description": "Item", "quantity": 1, "unit_cost": 50000}])
    svc.submit(pr.id, user=requester)
    with pytest.raises(NotEligibleApproverError):
        svc.approve(pr.id, user=small_approver)
    svc.approve(pr.id, user=large_approver)
    refreshed = db.session.get(PurchaseRequest, pr.id)
    assert refreshed.approval_instance.status == "APPROVED"


def test_mark_ordered_and_received(db, env):
    small_approver, large_approver, requester = env
    svc = PurchaseRequestService()
    pr = svc.create(description="Test", user=requester, lines=[
        {"item_description": "Item", "quantity": 1, "unit_cost": 500}])
    svc.submit(pr.id, user=requester)
    svc.approve(pr.id, user=small_approver)
    svc.mark_ordered(pr.id)
    assert db.session.get(PurchaseRequest, pr.id).status == "ORDERED"
    svc.mark_received(pr.id)
    assert db.session.get(PurchaseRequest, pr.id).status == "RECEIVED"
