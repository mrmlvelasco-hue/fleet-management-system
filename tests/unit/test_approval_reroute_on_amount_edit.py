"""Michael's bug report (2026-11-08): MO-2026-000050 was submitted with
estimated_cost 70,000 (a typo), which routed to a 3-level matrix going
up to a senior approver. He edited it down to 7,000 before anyone had
acted, expecting the lower amount to route through fewer levels -- but
the approval chain never changed; Level 1 was still "Awaiting" against
the original 3-level path.

docs/superpowers/specs/2026-10-12-document-edit-policy-design.md
explicitly declined "approval restart on edit" as out of scope -- by
design, an edit after submission notifies approvers and lists the
change (ChangedAfterSubmission) rather than re-routing. Confirmed with
Michael: that stands for a document with at least one level already
acted on, but a narrow override is wanted for the specific case here --
the routing amount changes AND zero levels have approved yet. In that
case, re-resolve the Approval Matrix with the new amount and rebuild
the chain from Level 1. Implemented once in BaseTransactionService /
ApprovalEngine (shared by every document type), proven here via two
representative modules: Purchase Request (its own `amount` column) and
Maintenance Order (`estimated_cost`).
"""
from datetime import date

import pytest

from app.core.approval.models import ApprovalAction, ApprovalInstance, ApprovalTask
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.approval_config.models import (
    ApprovalLevel, ApprovalMatrix, ApprovalPath)
from app.modules.document_config.models import DocumentType
from app.modules.document_config.service import DocumentTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.transactions.purchase_request.service import (
    PurchaseRequestService)
from app.modules.user_management.models import Permission, Role, User
from app.modules.master_data.org.service import DepartmentService


def _path(db, name, n_levels, role):
    path = ApprovalPath(name=name)
    db.session.add(path)
    db.session.flush()
    for lvl in range(1, n_levels + 1):
        db.session.add(ApprovalLevel(path_id=path.id, level_number=lvl,
                                     approver_type="ROLE", role_id=role.id))
    db.session.commit()
    return path


@pytest.fixture()
def env(db):
    sync_permissions()
    for code in ("PR", "MO"):
        if DocumentType.query.filter_by(code=code).first() is None:
            DocumentTypeService().create(code=code, name=code,
                                         requires_approval=True,
                                         auto_numbering=False)
    pr_dt = DocumentType.query.filter_by(code="PR").first()
    mo_dt = DocumentType.query.filter_by(code="MO").first()

    role = Role(name="rr-approver")
    role.permissions = Permission.query.all()
    db.session.add(role)
    db.session.commit()

    one_step = _path(db, "RR One-Step", 1, role)
    three_step = _path(db, "RR Three-Step", 3, role)

    for dt in (pr_dt, mo_dt):
        db.session.add(ApprovalMatrix(document_type_id=dt.id,
                                      approval_path_id=three_step.id,
                                      min_amount=50000))
        db.session.add(ApprovalMatrix(document_type_id=dt.id,
                                      approval_path_id=one_step.id,
                                      max_amount=49999))
    db.session.commit()

    branch = BranchService().create(code="BR-RR", name="RR Branch")
    dept = DepartmentService().create(code="RR-DEPT", name="RR Dept",
                                      branch_id=branch.id)
    vt = VehicleTypeService().create(code="LV-RR", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="RR-CM", name="Repair",
                                         category="CM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Camry", year=2024,
        branch_id=branch.id, conduction_number="RR-001")

    user = User(username="rr_initiator", email="rr@e.com",
               password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add(user)
    db.session.commit()

    return dict(vehicle=vehicle, dept=dept, mt=mt, user=user,
               one_step=one_step, three_step=three_step)


def test_mo_reroutes_to_fewer_levels_when_cost_corrected_before_any_approval(
        db, env):
    svc = MaintenanceOrderService()
    order = svc.create(vehicle_id=env["vehicle"].id,
                       scheduled_date=date(2026, 11, 10),
                       user=env["user"], maintenance_type_id=env["mt"].id,
                       estimated_cost=70000)
    svc.submit(order.id, env["user"])
    db.session.refresh(order)
    instance = order.approval_instance
    assert instance.approval_path_id == env["three_step"].id
    assert instance.current_level == 1

    svc.update(order.id, user=env["user"], estimated_cost=7000)
    db.session.refresh(order)
    db.session.refresh(instance)

    assert instance.approval_path_id == env["one_step"].id, (
        "Expected the chain to re-route to the 1-level path for 7,000 "
        "now that nothing has been approved yet."
    )
    assert instance.current_level == 1
    assert instance.amount == 7000

    pending = ApprovalTask.query.filter_by(
        approval_instance_id=instance.id, status="PENDING").all()
    assert len(pending) == 1
    assert pending[0].level_number == 1

    cancelled = ApprovalTask.query.filter_by(
        approval_instance_id=instance.id, status="CANCELLED").all()
    assert len(cancelled) == 1


def test_mo_does_not_reroute_once_a_level_has_already_approved(db, env):
    svc = MaintenanceOrderService()
    order = svc.create(vehicle_id=env["vehicle"].id,
                       scheduled_date=date(2026, 11, 10),
                       user=env["user"], maintenance_type_id=env["mt"].id,
                       estimated_cost=70000)
    svc.submit(order.id, env["user"])
    db.session.refresh(order)
    instance = order.approval_instance
    svc.approve(order.id, env["user"])
    db.session.refresh(instance)
    assert instance.current_level == 2  # advanced past level 1

    svc.update(order.id, user=env["user"], estimated_cost=7000)
    db.session.refresh(instance)

    # Unchanged: the documented "no restart" policy still governs once
    # any level has acted.
    assert instance.approval_path_id == env["three_step"].id
    assert instance.current_level == 2


def test_pr_also_reroutes_before_any_approval(db, env):
    svc = PurchaseRequestService()
    pr = svc.create(description="Parts", user=env["user"],
                    department_id=env["dept"].id,
                    lines=[{"item_description": "Widget", "quantity": 1,
                            "unit_cost": 70000}])
    svc.submit(pr.id, env["user"])
    db.session.refresh(pr)
    instance = pr.approval_instance
    assert instance.approval_path_id == env["three_step"].id

    svc.update(pr.id, user=env["user"], description="Parts, corrected")
    db.session.refresh(instance)
    # description isn't a routing field -- no reroute expected from this
    # edit alone, proving the trigger is specific to the amount field(s).
    assert instance.approval_path_id == env["three_step"].id


def test_pr_reroutes_when_a_line_is_corrected_before_any_approval(db, env):
    """PR's amount is derived from its lines (update()'s EDIT_FIELDS
    never includes it), so the reroute hook has to live in
    add_line/update_line/remove_line too, not just update()."""
    svc = PurchaseRequestService()
    pr = svc.create(description="Parts", user=env["user"],
                    department_id=env["dept"].id,
                    lines=[{"item_description": "Widget", "quantity": 1,
                            "unit_cost": 70000}])
    svc.submit(pr.id, env["user"])
    db.session.refresh(pr)
    instance = pr.approval_instance
    assert instance.approval_path_id == env["three_step"].id

    line = pr.lines[0]
    svc.update_line(line.id, unit_cost=7000)
    db.session.refresh(instance)
    assert instance.approval_path_id == env["one_step"].id
    assert instance.amount == 7000
