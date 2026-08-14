"""Auto Purchase Requisition from a Maintenance Order.

The requirement: the mechanic encodes the parts needing procurement
ONCE on the Maintenance Order, and those exact items become the
Purchase Request -- no second person re-typing the same list into a
second form. The PR is created as a DRAFT and deliberately NOT
submitted; the Fleet person still attaches quotations, checks budget
and submits it themselves.
"""
from datetime import date

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def order(app, db):
    from app.modules.master_data.org.service import (
        BranchService, DepartmentService)
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.models import User

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    admin = User.query.filter_by(username="admin").first()

    branch = BranchService().create(code="BR-APR", name="Branch APR")
    dept = DepartmentService().create(code="DP-APR", name="Fleet",
                                      branch_id=branch.id,
                                      cost_center="CC-APR-1")
    vt = VehicleTypeService().create(code="LV-APR", name="Light",
                                     category="LIGHT")
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Hilux", year=2022, branch_id=branch.id,
                                department_id=dept.id,
                                conduction_number="APR-1")
    mo = MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                          category="CORRECTIVE", scheduled_date=date.today(),
                          status="DRAFT", requested_by=admin.id,
                          description="Brake overhaul")
    db.session.add(mo)
    db.session.commit()
    return mo


def _svc():
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    return MaintenanceOrderService()


def test_parts_can_be_encoded_on_the_order(app, db, order):
    part = _svc().add_part(order.id, part_description="Brake Pad Set",
                           part_number="BP-1", quantity=2,
                           estimated_unit_cost=1250)
    assert part.id
    assert len(order.parts) == 1


def test_added_part_is_visible_immediately_in_the_same_session(
        app, db, order):
    """A real bug this guards: adding the row without appending to the
    relationship left order.parts stale, so PR generation saw an empty
    list and silently produced nothing."""
    _svc().add_part(order.id, part_description="Engine Oil", quantity=4,
                    estimated_unit_cost=280)
    assert len(order.parts) == 1, "parts collection went stale after add"


def test_pr_lines_mirror_the_encoded_parts_exactly(app, db, order):
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="Brake Pad Set",
                    part_number="BP-1", specification="Front, ceramic",
                    uom="set", quantity=2, estimated_unit_cost=1250)
    pr = _svc().generate_purchase_request(order.id, user=admin)
    assert len(pr.lines) == 1
    line = pr.lines[0]
    # The detail the mechanic supplied must survive into the PR -- a
    # buyer shopping from this line needs the part number and spec.
    assert "Brake Pad Set" in line.item_description
    assert "BP-1" in line.item_description
    assert "Front, ceramic" in line.item_description
    assert float(line.quantity) == 2
    assert float(line.unit_cost) == 1250


def test_generated_pr_is_a_draft_and_not_submitted(app, db, order):
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="Oil Filter",
                    quantity=1, estimated_unit_cost=300)
    pr = _svc().generate_purchase_request(order.id, user=admin)
    assert pr.status == "DRAFT", (
        "the Fleet person must still review and submit this themselves")


def test_order_links_to_the_generated_pr(app, db, order):
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="Oil Filter", quantity=1,
                    estimated_unit_cost=300)
    pr = _svc().generate_purchase_request(order.id, user=admin)
    db.session.refresh(order)
    assert order.purchase_request_id == pr.id


def test_pr_inherits_the_department_from_the_vehicle(app, db, order):
    """The cost centre chain: the PR must land against the right
    department without anyone re-selecting it."""
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="Oil Filter", quantity=1,
                    estimated_unit_cost=300)
    pr = _svc().generate_purchase_request(order.id, user=admin)
    assert pr.department_id == order.vehicle.department_id
    assert pr.department_id is not None


def test_generating_twice_does_not_create_a_duplicate_pr(app, db, order):
    """Approval events can fire more than once; a duplicate PR for one
    order would be a real problem to unpick downstream."""
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="Oil Filter", quantity=1,
                    estimated_unit_cost=300)
    first = _svc().generate_purchase_request(order.id, user=admin)
    second = _svc().generate_purchase_request(order.id, user=admin)
    assert first.id == second.id


def test_an_order_with_no_parts_generates_nothing(app, db, order):
    """Plenty of maintenance needs no procurement -- that is a normal
    outcome, not an error."""
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    assert _svc().generate_purchase_request(order.id, user=admin) is None
    db.session.refresh(order)
    assert order.purchase_request_id is None


def test_parts_cannot_be_added_once_the_order_leaves_draft(app, db, order):
    """The parts list is what the approver approved and what the PR was
    built from -- it must not change afterwards."""
    from app.modules.transactions.maintenance_order.service import (
        InvalidOrderStateError)
    order.status = "IN_PROGRESS"
    db.session.commit()
    with pytest.raises(InvalidOrderStateError):
        _svc().add_part(order.id, part_description="Late Part", quantity=1)


def test_approval_event_triggers_generation(app, db, order):
    """The actual trigger: final approval, not a manual button."""
    from app.modules.transactions.maintenance_order.auto_pr import (
        _generate_for_order)
    _svc().add_part(order.id, part_description="Engine Oil", quantity=4,
                    estimated_unit_cost=280)
    pr = _generate_for_order(order.id)
    assert pr is not None
    db.session.refresh(order)
    assert order.purchase_request_id == pr.id


def test_a_pr_failure_never_blocks_the_approval(app, db, order, monkeypatch):
    """An approval that has been legitimately granted must stand even if
    PR creation fails -- the order stays approved and the PR can be
    raised manually."""
    from app.modules.transactions.maintenance_order import auto_pr
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    _svc().add_part(order.id, part_description="Engine Oil", quantity=4,
                    estimated_unit_cost=280)

    def boom(self, order_id, user):
        raise RuntimeError("numbering service unavailable")
    monkeypatch.setattr(MaintenanceOrderService,
                        "generate_purchase_request", boom)
    # Must swallow the failure rather than propagate it into the
    # approval transaction.
    assert auto_pr._generate_for_order(order.id) is None


def test_parts_section_renders_on_the_order_page(app, db, order):
    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"},
                follow_redirects=True)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}").get_data(as_text=True)
    assert "Parts to Procure" in html
    assert 'id="moPartForm"' in html


# ── Route-level tests ────────────────────────────────────────────────────
#
# These exist because a real 500 shipped that the service-level tests
# above could never have caught: the add-part route called a method
# (get_by_id) that lives on a DIFFERENT service class in the same
# module. Every test called the service directly, so the route body was
# never executed at all. Testing the service is not testing the route.

def _logged_in(app):
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_add_part_via_the_route_returns_json(app, db, order):
    client = _logged_in(app)
    r = client.post(f"/transactions/maintenance-orders/{order.id}/parts",
                    data={"part_description": "Labor", "quantity": "1",
                         "estimated_unit_cost": "2000"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200, f"route returned {r.status_code}"
    body = r.get_json()
    assert body["ok"] is True
    assert body["part"]["part_description"] == "Labor"
    assert body["parts_total"] == "2,000.00"


def test_add_part_via_the_route_without_ajax_redirects(app, db, order):
    client = _logged_in(app)
    r = client.post(f"/transactions/maintenance-orders/{order.id}/parts",
                    data={"part_description": "Labor", "quantity": "1",
                         "estimated_unit_cost": "2000"})
    assert r.status_code == 302


def test_remove_part_via_the_route(app, db, order):
    client = _logged_in(app)
    add = client.post(f"/transactions/maintenance-orders/{order.id}/parts",
                      data={"part_description": "Oil", "quantity": "2",
                           "estimated_unit_cost": "100"},
                      headers={"X-Requested-With": "XMLHttpRequest"})
    part_id = add.get_json()["part"]["id"]
    r = client.post(
        f"/transactions/maintenance-orders/{order.id}/parts/{part_id}/delete",
        headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    assert r.get_json()["parts_total"] == "0.00"


def test_add_part_route_rejects_a_non_draft_order(app, db, order):
    client = _logged_in(app)
    order.status = "IN_PROGRESS"
    db.session.commit()
    r = client.post(f"/transactions/maintenance-orders/{order.id}/parts",
                    data={"part_description": "Late", "quantity": "1",
                         "estimated_unit_cost": "10"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 409
    assert r.get_json()["ok"] is False


def test_add_part_route_rejects_bad_numbers_without_a_500(app, db, order):
    client = _logged_in(app)
    r = client.post(f"/transactions/maintenance-orders/{order.id}/parts",
                    data={"part_description": "Oil", "quantity": "abc",
                         "estimated_unit_cost": "10"},
                    headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 400, f"got {r.status_code}, expected a clean 400"


# ── Manual generation for orders that missed the automatic hook ──────────
#
# Reported: an approved order showed its parts list and the "PR
# generated on final approval" badge, but no PR. Automatic generation
# fires on the approval EVENT, so it only ever covers orders approved
# AFTER the feature was installed -- an order approved before then had
# no way to get its PR at all, leaving the parts list stranded and the
# duplication this feature removes back in play.

def test_pr_requester_is_the_orders_requester_not_the_approver(
        app, db, order):
    """The PR must name the person who stated the requirement, not
    whoever happened to click approve."""
    from app.modules.user_management.models import User
    admin = User.query.filter_by(username="admin").first()
    _svc().add_part(order.id, part_description="4L Oil", quantity=1,
                    estimated_unit_cost=4520)
    pr = _svc().generate_purchase_request(order.id, user=order.requester)
    assert pr.requested_by == order.requested_by
    assert pr.requested_by == admin.id


def test_generate_button_offered_for_an_approved_order_without_a_pr(
        app, db, order):
    _svc().add_part(order.id, part_description="4L Oil", quantity=1,
                    estimated_unit_cost=4520)
    order.status = "APPROVED"
    db.session.commit()
    client = _logged_in(app)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}").get_data(as_text=True)
    assert "Generate Purchase Request" in html


def test_generate_button_not_offered_while_still_draft(app, db, order):
    """A draft order will get its PR automatically on approval --
    offering the button there would create it early, before anyone has
    approved the parts list."""
    _svc().add_part(order.id, part_description="4L Oil", quantity=1,
                    estimated_unit_cost=4520)
    client = _logged_in(app)
    html = client.get(
        f"/transactions/maintenance-orders/{order.id}").get_data(as_text=True)
    assert "Generate Purchase Request" not in html


def test_manual_generation_creates_the_pr(app, db, order):
    _svc().add_part(order.id, part_description="4L Oil",
                    part_number="OIL0001", specification="5W40", uom="GAL",
                    quantity=1, estimated_unit_cost=4520)
    order.status = "APPROVED"
    db.session.commit()
    client = _logged_in(app)
    client.post(f"/transactions/maintenance-orders/{order.id}/generate-pr",
                follow_redirects=True)
    db.session.refresh(order)
    assert order.purchase_request_id is not None
    pr = order.purchase_request
    assert pr.status == "DRAFT"
    assert "OIL0001" in pr.lines[0].item_description


def test_manual_generation_is_idempotent(app, db, order):
    _svc().add_part(order.id, part_description="4L Oil", quantity=1,
                    estimated_unit_cost=4520)
    order.status = "APPROVED"
    db.session.commit()
    client = _logged_in(app)
    client.post(f"/transactions/maintenance-orders/{order.id}/generate-pr",
                follow_redirects=True)
    db.session.refresh(order)
    first = order.purchase_request_id
    client.post(f"/transactions/maintenance-orders/{order.id}/generate-pr",
                follow_redirects=True)
    db.session.refresh(order)
    assert order.purchase_request_id == first


def test_manual_generation_refuses_when_there_are_no_parts(app, db, order):
    order.status = "APPROVED"
    db.session.commit()
    client = _logged_in(app)
    r = client.post(
        f"/transactions/maintenance-orders/{order.id}/generate-pr",
        follow_redirects=True)
    assert r.status_code == 200
    db.session.refresh(order)
    assert order.purchase_request_id is None
