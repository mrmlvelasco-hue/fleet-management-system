from datetime import date
from decimal import Decimal

import pytest

from app.modules.approval_config.service import (
    ApprovalPathService, ApprovalMatrixService, MatrixOverlapError, NoMatrixError)
from app.modules.document_config.service import DocumentTypeService
from app.modules.user_management.models import Role


@pytest.fixture()
def setup(db):
    role = Role(name="Approver")
    db.session.add(role)
    db.session.commit()
    dt = DocumentTypeService().create(code="PR", name="Purchase Request",
                                      requires_approval=True)
    path = ApprovalPathService().create(name="One-Step", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": role.id}])
    return dt, path


def test_resolve_amount_in_range(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    m = svc.create(dt.id, path.id, min_amount=0, max_amount=10000)
    assert svc.resolve(dt.id, amount=5000).id == m.id


def test_resolve_open_max(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    svc.create(dt.id, path.id, min_amount=0, max_amount=10000)
    m2 = svc.create(dt.id, path.id, min_amount=Decimal("10000.01"),
                    max_amount=None)
    assert svc.resolve(dt.id, amount=999999).id == m2.id


def test_resolve_amount_independent(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    m = svc.create(dt.id, path.id)  # both bounds NULL
    assert svc.resolve(dt.id, amount=None).id == m.id


def test_no_match_raises(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    svc.create(dt.id, path.id, min_amount=0, max_amount=100)
    with pytest.raises(NoMatrixError):
        svc.resolve(dt.id, amount=500)


def test_overlap_rejected(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    svc.create(dt.id, path.id, min_amount=0, max_amount=10000)
    with pytest.raises(MatrixOverlapError):
        svc.create(dt.id, path.id, min_amount=5000, max_amount=20000)


def test_effective_dates_respected(db, setup):
    dt, path = setup
    svc = ApprovalMatrixService()
    m_old = svc.create(dt.id, path.id, min_amount=0, max_amount=10000,
                       effective_to=date(2025, 12, 31))
    m_new = svc.create(dt.id, path.id, min_amount=0, max_amount=10000,
                       effective_from=date(2026, 1, 1))
    assert svc.resolve(dt.id, amount=100, on_date=date(2025, 6, 1)).id == m_old.id
    assert svc.resolve(dt.id, amount=100, on_date=date(2026, 6, 1)).id == m_new.id
