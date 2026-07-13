from datetime import date

import pytest

from app.modules.transactions.tire_txn.service import TireTransactionService
from app.modules.transactions.battery_txn.service import BatteryTransactionService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.vendor.service import VendorService
from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.user_management.models import User, Role


@pytest.fixture()
def env(db):
    role = Role(name="Tire Battery Approver")
    approver = User(username="tb_approver", email="tb_approver@x.com",
                    password_hash="x")
    approver.roles.append(role)
    requester = User(username="tb_requester", email="tb_requester@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([role, approver, requester])
    _db.session.commit()

    vendor = VendorService().create(code="VEN-TBAPPR", name="TB Approval Vendor")
    tire = TireService().create(serial_number="TIRE-TBAPPR", brand="Bridgestone",
                                size="185/65R15", tire_type="RADIAL",
                                vendor_id=vendor.id)
    battery = BatteryService().create(serial_number="BATT-TBAPPR",
                                      brand="Motolite", vendor_id=vendor.id)

    return role, approver, requester, tire, battery


def _setup_approval(code, name, role, requires_approval=True):
    dt = DocumentTypeService().create(code=code, name=name,
                                      requires_approval=requires_approval,
                                      auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    if requires_approval:
        path = ApprovalPathService().create(name=f"{code} Path", levels=[
            {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
        ApprovalMatrixService().create(dt.id, path.id, min_amount=None,
                                       max_amount=None)
    return dt


def test_tire_txn_is_draft_when_document_type_requires_approval(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("TIR", "Tire Transaction", role)
    txn = TireTransactionService().create(
        tire_id=tire.id, action="MOUNT", transaction_date=date.today(),
        user=requester)
    assert txn.status == "DRAFT"
    # Physical effect not applied yet — tire status unchanged
    assert TireService().get(tire.id).status == "IN_STOCK"


def test_tire_txn_stays_completed_when_no_approval_required(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("TIR", "Tire Transaction", role, requires_approval=False)
    txn = TireTransactionService().create(
        tire_id=tire.id, action="MOUNT", transaction_date=date.today(),
        user=requester)
    assert txn.status == "COMPLETED"
    assert TireService().get(tire.id).status == "MOUNTED"


def test_tire_txn_applies_physical_effect_on_final_approval(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("TIR", "Tire Transaction", role)
    svc = TireTransactionService()
    txn = svc.create(tire_id=tire.id, action="MOUNT",
                     transaction_date=date.today(), user=requester)
    svc.submit(txn.id, user=requester)
    svc.approve(txn.id, user=approver)

    updated_txn = svc.get(txn.id)
    assert updated_txn.status == "COMPLETED"
    assert TireService().get(tire.id).status == "MOUNTED"


def test_tire_txn_not_applied_if_rejected(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("TIR", "Tire Transaction", role)
    svc = TireTransactionService()
    txn = svc.create(tire_id=tire.id, action="MOUNT",
                     transaction_date=date.today(), user=requester)
    svc.submit(txn.id, user=requester)
    svc.reject(txn.id, user=approver, remarks="not needed")

    assert TireService().get(tire.id).status == "IN_STOCK"


def test_battery_txn_is_draft_when_document_type_requires_approval(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("BAT", "Battery Transaction", role)
    txn = BatteryTransactionService().create(
        battery_id=battery.id, action="MOUNT", transaction_date=date.today(),
        user=requester)
    assert txn.status == "DRAFT"
    assert BatteryService().get(battery.id).status == "IN_STOCK"


def test_battery_txn_applies_physical_effect_on_final_approval(db, env):
    role, approver, requester, tire, battery = env
    _setup_approval("BAT", "Battery Transaction", role)
    svc = BatteryTransactionService()
    txn = svc.create(battery_id=battery.id, action="MOUNT",
                     transaction_date=date.today(), user=requester)
    svc.submit(txn.id, user=requester)
    svc.approve(txn.id, user=approver)

    assert svc.get(txn.id).status == "COMPLETED"
    assert BatteryService().get(battery.id).status == "MOUNTED"
