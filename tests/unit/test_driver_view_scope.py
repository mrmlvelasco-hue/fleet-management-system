from datetime import date

import pytest

from app.modules.master_data.driver.service import DriverService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-MNL-DRV", name="Manila Driver")
    cebu = BranchService().create(code="BR-CEB-DRV", name="Cebu Driver")

    manila_driver = DriverService().create(
        employee_number="EMP-MNL1", first_name="Ana", last_name="Reyes",
        license_number="LIC-MNL1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=manila.id)
    cebu_driver = DriverService().create(
        employee_number="EMP-CEB1", first_name="Ben", last_name="Cruz",
        license_number="LIC-CEB1", license_expiry=date(2030, 1, 1),
        license_type="PROFESSIONAL", branch_id=cebu.id)

    manila_user = User(username="drvscope_manila", email="drvscope_manila@x.com",
                       password_hash="x")
    cebu_user = User(username="drvscope_cebu", email="drvscope_cebu@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([manila_user, cebu_user])
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)
    UserOrgScopeService().assign(cebu_user.id, scope_type="BRANCH",
                                branch_id=cebu.id)

    return manila, cebu, manila_driver, cebu_driver, manila_user, cebu_user


def test_manila_user_does_not_see_cebu_driver(db, env):
    manila, cebu, manila_driver, cebu_driver, manila_user, cebu_user = env
    visible = DriverService().list(user=manila_user)
    visible_ids = {d.id for d in visible}
    assert manila_driver.id in visible_ids
    assert cebu_driver.id not in visible_ids


def test_no_scope_user_sees_all_drivers(db, env):
    manila, cebu, manila_driver, cebu_driver, manila_user, cebu_user = env
    unscoped = User(username="drvscope_unscoped", email="drvscope_unscoped@x.com",
                    password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    visible = DriverService().list(user=unscoped)
    visible_ids = {d.id for d in visible}
    assert manila_driver.id in visible_ids
    assert cebu_driver.id in visible_ids


def test_get_visible_blocks_out_of_scope_driver(db, env):
    manila, cebu, manila_driver, cebu_driver, manila_user, cebu_user = env
    assert DriverService().get_visible(cebu_driver.id, manila_user) is None
    assert DriverService().get_visible(manila_driver.id, manila_user) is not None


def test_list_without_user_arg_unchanged(db, env):
    manila, cebu, manila_driver, cebu_driver, manila_user, cebu_user = env
    visible = DriverService().list()
    visible_ids = {d.id for d in visible}
    assert manila_driver.id in visible_ids
    assert cebu_driver.id in visible_ids
