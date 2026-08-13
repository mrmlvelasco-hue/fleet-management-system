"""Transaction list visibility enforced in SQL rather than in Python.

BaseTransactionService.list() used to load every row of a transaction
table and then filter in Python -- and the comment-participant rule ran
one extra query PER ROW. On a table with 1,000 orders that was ~1,001
queries and every row loaded, before a single one was shown.

The rules themselves are unchanged; only where they are evaluated
changed. These tests exist to prove that: each of the four original
visibility rules is asserted independently, so a SQL translation that
quietly widened or narrowed access would fail here.
"""
from datetime import date

import pytest

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def world(app, db):
    """Two branches, a vehicle in each, and an order against each."""
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.user_management.models import User
    from app.core.security.password import hash_password

    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")

    manila = BranchService().create(code="BR-MNL", name="Manila")
    cebu = BranchService().create(code="BR-CEB", name="Cebu")
    vt = VehicleTypeService().create(code="LV-VIS", name="Light",
                                     category="LIGHT")
    v_mnl = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                    model="Hilux", year=2022,
                                    branch_id=manila.id,
                                    conduction_number="VIS-MNL")
    v_ceb = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                    model="Hilux", year=2022,
                                    branch_id=cebu.id,
                                    conduction_number="VIS-CEB")

    owner = User(username="owner", email="owner@x.com",
                 password_hash=hash_password("Testpass123!"),
                 must_change_password=False)
    viewer = User(username="viewer", email="viewer@x.com",
                  password_hash=hash_password("Testpass123!"),
                  must_change_password=False)
    db.session.add_all([owner, viewer])
    db.session.commit()

    mo_mnl = MaintenanceOrder(vehicle_id=v_mnl.id,
                              order_category="MAINTENANCE",
                              scheduled_date=date.today(), status="DRAFT",
                              requested_by=owner.id)
    mo_ceb = MaintenanceOrder(vehicle_id=v_ceb.id,
                              order_category="MAINTENANCE",
                              scheduled_date=date.today(), status="DRAFT",
                              requested_by=owner.id)
    db.session.add_all([mo_mnl, mo_ceb])
    db.session.commit()
    return {"manila": manila, "cebu": cebu, "owner": owner,
            "viewer": viewer, "mo_mnl": mo_mnl, "mo_ceb": mo_ceb}


def _svc():
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    return MaintenanceOrderService()


def _scope(db, user, branch, scope_type="BRANCH"):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    UserOrgScopeService().assign(
        user.id, scope_type=scope_type,
        branch_id=branch.id if branch else None)
    db.session.commit()


# ── The four original rules, each asserted independently ────────────────

def test_no_user_means_no_filtering(app, db, world):
    ids = {m.id for m in _svc().list(user=None)}
    assert world["mo_mnl"].id in ids
    assert world["mo_ceb"].id in ids


def test_user_with_no_scope_rows_still_sees_everything(app, db, world):
    """Rollout safety: turning org-scoping on must not silently hide
    every record from everyone who hasn't been assigned a scope yet."""
    ids = {m.id for m in _svc().list(user=world["viewer"])}
    assert world["mo_mnl"].id in ids
    assert world["mo_ceb"].id in ids


def test_branch_scope_hides_other_branches(app, db, world):
    _scope(db, world["viewer"], world["manila"])
    ids = {m.id for m in _svc().list(user=world["viewer"])}
    assert world["mo_mnl"].id in ids
    assert world["mo_ceb"].id not in ids


def test_requester_sees_their_own_record_outside_their_branch(
        app, db, world):
    """The owner raised both orders but is scoped only to Manila -- they
    must still see the Cebu one they raised themselves."""
    _scope(db, world["owner"], world["manila"])
    ids = {m.id for m in _svc().list(user=world["owner"])}
    assert world["mo_ceb"].id in ids


def test_comment_participant_sees_a_record_outside_their_branch(
        app, db, world):
    """Being addressed in a comment makes someone a participant in that
    document regardless of branch -- otherwise they couldn't open the
    page to read the comment that mentioned them."""
    from app.core.comments.models import DocumentComment
    db.session.add(DocumentComment(
        reference_table="maintenance_orders",
        reference_id=world["mo_ceb"].id,
        author_id=world["owner"].id,
        recipient_id=world["viewer"].id,
        body="Please review this one."))
    db.session.commit()
    _scope(db, world["viewer"], world["manila"])
    ids = {m.id for m in _svc().list(user=world["viewer"])}
    assert world["mo_ceb"].id in ids, (
        "a comment recipient lost access to the document mentioning them")


def test_global_scope_sees_everything(app, db, world):
    _scope(db, world["viewer"], None, scope_type="GLOBAL")
    ids = {m.id for m in _svc().list(user=world["viewer"])}
    assert world["mo_mnl"].id in ids
    assert world["mo_ceb"].id in ids


# ── The actual point: query count no longer scales with row count ───────

def test_query_count_does_not_grow_with_the_number_of_records(
        app, db, world):
    """The regression this whole change exists to prevent. Previously
    the comment-participant rule ran one query per row, so this count
    grew linearly with the table."""
    from sqlalchemy import event
    from app.extensions import db as _db
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)

    _scope(db, world["viewer"], world["manila"])

    def count_queries():
        seen = []
        def _rec(conn, cur, stmt, params, ctx, many):
            seen.append(stmt)
        event.listen(_db.engine, "before_cursor_execute", _rec)
        _svc().list(user=world["viewer"])
        event.remove(_db.engine, "before_cursor_execute", _rec)
        return len(seen)

    baseline = count_queries()

    # Add 50 more orders in the visible branch.
    from app.modules.master_data.vehicle.models import Vehicle
    v = Vehicle.query.filter_by(conduction_number="VIS-MNL").first()
    _db.session.bulk_save_objects([
        MaintenanceOrder(vehicle_id=v.id, order_category="MAINTENANCE",
                         scheduled_date=date.today(), status="DRAFT",
                         requested_by=world["owner"].id)
        for _ in range(50)])
    _db.session.commit()

    after = count_queries()
    assert after == baseline, (
        f"query count grew from {baseline} to {after} after adding 50 rows "
        f"-- visibility filtering is back in Python")
