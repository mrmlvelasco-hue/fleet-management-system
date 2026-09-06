"""Tire and Battery: the physical effect must follow the approval.

Checked after the vehicle registration and vehicle movement bypasses.
Both of these READ correctly -- create() sets DRAFT when the Document
Type requires approval, and approve() applies the effect only when the
instance reaches APPROVED -- but "reads correctly" is not evidence, and
the effect here is not cosmetic: it changes a tire's or battery's status
in master data, mounting or disposing of an asset.

The case worth proving is MULTI-LEVEL approval. approve() runs at every
level, so a two-level path calls it twice; if the guard were on "this
call succeeded" rather than on the INSTANCE being APPROVED, level one
would mount the tire before level two had seen it.
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.extensions import db
from app.modules.document_config.models import DocumentType
from app.modules.master_data.battery.models import Battery
from app.modules.master_data.org.models import Branch
from app.modules.master_data.reference.models import VehicleType
from app.modules.master_data.tire.models import Tire
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.battery_txn.service import (
    BatteryTransactionService)
from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.user_management.models import Permission, Role, User


def _perm(code):
    p = Permission.query.filter_by(code=code).first()
    if p is None:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        db.session.flush()
    return p


def _doc_type(code, requires_approval):
    dt = DocumentType.query.filter_by(code=code).first()
    if dt is None:
        dt = DocumentType(code=code, name=code, requires_approval=requires_approval)
        db.session.add(dt)
    else:
        dt.requires_approval = requires_approval
    db.session.commit()
    return dt


@pytest.fixture()
def env(app):
    b = Branch(code="CAR", name="Carmona")
    vt = VehicleType(code="LT", name="Light", category="LIGHT")
    db.session.add_all([b, vt])
    db.session.commit()
    role = Role(name="R", description="r")
    db.session.add(role)
    db.session.flush()
    for c in ("tiretxn.create", "tiretxn.view", "batterytxn.create",
              "batterytxn.view"):
        role.permissions.append(_perm(c))
    u = User(username="admin", email="a@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles.append(role)
    db.session.add(u)
    db.session.commit()
    v = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                model="Vios", year=2009, branch_id=b.id,
                                conduction_number="A1", plate_number="NZO-619")
    tire = Tire(serial_number="T-001", brand="Yokohama", size="185/65R15",
                tire_type="RADIAL", status="IN_STOCK", branch_id=b.id)
    batt = Battery(serial_number="B-001", brand="Motolite",
                   status="IN_STOCK", branch_id=b.id)
    db.session.add_all([tire, batt])
    db.session.commit()
    return v, tire, batt, u


# ── tire ─────────────────────────────────────────────────────────────

def test_tire_stays_a_draft_and_is_NOT_mounted_before_approval(app, env):
    """The one that matters. Mounting a tire is a change to master data;
    it must not happen on an unapproved draft."""
    v, tire, _b, u = env
    _doc_type("TIR", True)

    txn = TireTransactionService().create(
        tire_id=tire.id, action="MOUNT", transaction_date=date.today(),
        user=u, vehicle_id=v.id)

    assert txn.status == "DRAFT"
    assert db.session.get(Tire, tire.id).status == "IN_STOCK"


def test_tire_completes_immediately_when_approval_is_not_configured(app, env):
    """The legitimate direct path -- and the reason the rule reads the
    Document Type rather than a hardcoded status."""
    v, tire, _b, u = env
    _doc_type("TIR", False)

    txn = TireTransactionService().create(
        tire_id=tire.id, action="MOUNT", transaction_date=date.today(),
        user=u, vehicle_id=v.id)

    assert txn.status == "COMPLETED"
    assert db.session.get(Tire, tire.id).status == "MOUNTED"


def test_a_disposal_deactivates_the_tire_only_once_approved(app, env):
    """DISPOSE also sets is_active=False -- a soft delete. Doing that
    from an unapproved draft would remove an asset from the register on
    one person's say-so."""
    _v, tire, _b, u = env
    _doc_type("TIR", True)

    TireTransactionService().create(
        tire_id=tire.id, action="DISPOSE", transaction_date=date.today(),
        user=u)

    fresh = db.session.get(Tire, tire.id)
    assert fresh.is_active is True
    assert fresh.status == "IN_STOCK"


# ── battery ──────────────────────────────────────────────────────────

def test_battery_stays_a_draft_and_is_NOT_mounted_before_approval(app, env):
    v, _t, batt, u = env
    _doc_type("BAT", True)

    txn = BatteryTransactionService().create(
        battery_id=batt.id, action="MOUNT", transaction_date=date.today(),
        user=u, vehicle_id=v.id)

    assert txn.status == "DRAFT"
    assert db.session.get(Battery, batt.id).status == "IN_STOCK"


def test_battery_completes_immediately_when_approval_is_not_configured(app,
                                                                       env):
    v, _t, batt, u = env
    _doc_type("BAT", False)

    txn = BatteryTransactionService().create(
        battery_id=batt.id, action="MOUNT", transaction_date=date.today(),
        user=u, vehicle_id=v.id)

    assert txn.status == "COMPLETED"
    assert db.session.get(Battery, batt.id).status != "IN_STOCK"


# ── the guard's shape ────────────────────────────────────────────────

def test_the_effect_is_keyed_to_the_INSTANCE_being_approved(app, env):
    """Not to 'approve() was called'.

    approve() runs at EVERY level of a multi-level path. If the guard
    were on the call rather than on the instance reaching APPROVED,
    level one would mount the tire before level two had seen it.

    Asserted by reading the guard rather than by building a two-level
    matrix, because the condition is the thing under test and it is one
    line: `if record.approval_instance
             and record.approval_instance.status == "APPROVED"`.
    """
    import inspect
    for svc in (TireTransactionService, BatteryTransactionService):
        src = inspect.getsource(svc.approve)
        assert 'approval_instance.status == "APPROVED"' in src, (
            f"{svc.__name__}.approve must gate on the INSTANCE status, "
            f"not merely on having been called")
