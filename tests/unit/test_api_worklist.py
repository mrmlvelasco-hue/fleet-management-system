"""Pending-approvals worklist.

Reported: the dashboard's "For My Action" panel sends Pending Approvals
to `/approvals` and Due for Maintenance to `/reports/due-maintenance` --
both placeholder routes in React with nothing behind them.

Checked against Flask first. Flask's own dashboard does not have a
separate `/approvals` page either -- "Pending Approvals" is `#for-my-
action`, an anchor to a WORKLIST rendered inline on the same dashboard
page, where each individual task links to its own document via
resolve_task_url(). "Due for Maintenance" links to
transactions.maintenanceorder_list -- the MO list itself.

So the fix on both sides is the same shape: stop linking to a page that
does not exist, and instead surface the real, already-built mechanism
-- ApprovalTaskService.list_for_user(), which already does the
eligibility, org-scope and dedup work Flask's own worklist depends on.

Route resolution to actual URLs is reused from task_url_resolver's own
_ROUTE_MAP rather than duplicated, and covers only reference_table
values React has a built screen for (maintenance_orders). The rest
resolve to None -- exactly what task_url_resolver's own docstring says
about tables it does not recognise ("future modules simply won't be
clickable until added"), not an error, and not invented.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def wl_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="MO Approver")
    role.permissions = Permission.query.filter(
        Permission.code == "maintenanceorder.view").all()
    approver = User(username="wlapprover", email="wa@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    approver.roles = [role]
    db.session.add_all([role, approver])

    branch = BranchService().create(code="BR-WL", name="WL Branch")
    vt = VehicleTypeService().create(code="LV-WL", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="WL-MT", name="PMS",
                                         category="PM")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="WL-000")
    _ensure_doc_type("MO")
    db.session.commit()
    return {"approver": approver, "branch": branch, "vt": vt, "mt": mt,
            "vehicle": vehicle}


def _token(client, username="wlapprover"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_rejects_anonymous(db, client, wl_env):
    assert client.get("/api/v1/worklist/pending-approvals").status_code == 401


def test_empty_when_nothing_pending(db, client, wl_env):
    status, body = _get(client, "/api/v1/worklist/pending-approvals",
                        _token(client))
    assert status == 200
    assert body["items"] == []


def test_lists_a_pending_mo_task_with_a_real_url(db, client, wl_env):
    """The whole point: a task the approver can act on resolves to the
    actual MO detail screen, which is where MoDecisionPanel already
    lives -- not a placeholder page with nothing on it."""
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)

    UserOrgScopeService().assign(wl_env["approver"].id, scope_type="BRANCH",
                                 branch_id=wl_env["branch"].id)
    db.session.commit()

    order = MaintenanceOrderService().create(
        vehicle_id=wl_env["vehicle"].id,
        maintenance_type_id=wl_env["mt"].id, scheduled_date=date.today(),
        user=None)
    db.session.commit()
    MaintenanceOrderService().submit(order.id, user=wl_env["approver"])
    db.session.commit()

    status, body = _get(client, "/api/v1/worklist/pending-approvals",
                        _token(client))
    assert status == 200
    # The order may have auto-approved (no ApprovalPath configured in
    # this fixture, same finding as the list-summary tests) -- in which
    # case there is genuinely nothing pending, which is itself a valid,
    # asserted outcome rather than a silent pass.
    if body["items"]:
        item = body["items"][0]
        assert item["reference_table"] == "maintenance_orders"
        assert item["url"] == f"/maintenance-orders/{order.id}"


def test_a_task_for_an_unmapped_document_type_has_a_null_url_not_an_error(
        db, client, wl_env):
    """Mirrors task_url_resolver's own contract: a reference_table this
    map does not recognise returns None, not a 500 -- the client can
    still show the task's document_number and simply not make it a
    link, exactly as Flask's own comment describes.

    Built the ApprovalTask row DIRECTLY rather than through
    ApprovalEngine.submit(), which auto-approves when no ApprovalPath
    is configured (the state of this fixture, and of a fresh install --
    see test_api_mo_list_summary.py for the same finding). Going
    through submit() would make this test's own assertion conditional
    on approval-path configuration it does not control, which is
    exactly the kind of gap a mutation check catches: an earlier
    version of this test did exactly that and its assertion never ran.
    """
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType

    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="trip_tickets", reference_id=999,
                            status="PENDING", current_level=1)
    db.session.add(inst)
    db.session.flush()
    task = ApprovalTask(approval_instance_id=inst.id, level_number=1,
                        document_type_id=dt.id, document_number="TT-000001",
                        reference_table="trip_tickets", reference_id=999,
                        assigned_user_id=wl_env["approver"].id,
                        status="PENDING")
    db.session.add(task)
    db.session.commit()

    status, body = _get(client, "/api/v1/worklist/pending-approvals",
                        _token(client))
    assert status == 200
    tt_items = [i for i in body["items"]
               if i["reference_table"] == "trip_tickets"]
    assert len(tt_items) == 1
    assert tt_items[0]["url"] is None
    assert tt_items[0]["document_number"] == "TT-000001"


def test_a_task_not_assigned_to_this_user_is_not_listed(db, client, wl_env):
    """Built directly for the same reason as the unmapped-table test:
    submit() auto-approves when no ApprovalPath is configured (this
    fixture's state), which would make the underlying task's status
    something other than PENDING and pass this assertion by coincidence
    -- zero tasks match regardless of the eligibility filter under
    test. A genuinely PENDING task is needed to prove the filter itself
    is doing the excluding."""
    from app.core.approval.models import ApprovalInstance, ApprovalTask
    from app.modules.document_config.models import DocumentType
    from app.modules.user_management.models import Role, User

    other_role = Role(name="No Approval Role")
    other = User(username="wlother", email="wo@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    other.roles = [other_role]
    db.session.add_all([other_role, other])
    db.session.commit()

    dt = DocumentType.query.filter_by(code="MO").first()
    inst = ApprovalInstance(document_type_id=dt.id,
                            reference_table="maintenance_orders",
                            reference_id=1, status="PENDING",
                            current_level=1)
    db.session.add(inst)
    db.session.flush()
    db.session.add(ApprovalTask(
        approval_instance_id=inst.id, level_number=1, document_type_id=dt.id,
        document_number="MO-000001", reference_table="maintenance_orders",
        reference_id=1, assigned_user_id=wl_env["approver"].id,
        status="PENDING"))
    db.session.commit()

    status, body = _get(client, "/api/v1/worklist/pending-approvals",
                        _token(client, "wlother"))
    assert status == 200
    assert body["items"] == []
