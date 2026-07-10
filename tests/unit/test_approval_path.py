import pytest

from app.modules.approval_config.service import (
    ApprovalPathService, InvalidPathError)
from app.modules.user_management.models import Role


@pytest.fixture()
def roles(db):
    r1, r2 = Role(name="Supervisor"), Role(name="Fleet Manager")
    db.session.add_all([r1, r2])
    db.session.commit()
    return r1, r2


def test_create_path_with_levels(db, roles):
    r1, r2 = roles
    svc = ApprovalPathService()
    path = svc.create(name="Two-Step", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": r1.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": r2.id},
    ])
    assert [l.level_number for l in path.levels] == [1, 2]


def test_empty_path_rejected(db):
    with pytest.raises(InvalidPathError):
        ApprovalPathService().create(name="Empty", levels=[])


def test_non_contiguous_levels_rejected(db, roles):
    r1, _ = roles
    with pytest.raises(InvalidPathError):
        ApprovalPathService().create(name="Gappy", levels=[
            {"level_number": 1, "approver_type": "ROLE", "role_id": r1.id},
            {"level_number": 3, "approver_type": "ROLE", "role_id": r1.id},
        ])


def test_level_requires_role_or_user(db):
    with pytest.raises(InvalidPathError):
        ApprovalPathService().create(name="Broken", levels=[
            {"level_number": 1, "approver_type": "ROLE", "role_id": None},
        ])


def test_update_replaces_levels(db, roles):
    r1, r2 = roles
    svc = ApprovalPathService()
    path = svc.create(name="Solo", levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": r1.id}])
    svc.update(path.id, levels=[
        {"level_number": 1, "approver_type": "ROLE", "role_id": r2.id},
        {"level_number": 2, "approver_type": "ROLE", "role_id": r1.id}])
    assert len(path.levels) == 2
    assert path.levels[0].role_id == r2.id
