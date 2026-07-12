import pytest

from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-MNL-TIRE", name="Manila Tire")
    cebu = BranchService().create(code="BR-CEB-TIRE", name="Cebu Tire")

    manila_tire = TireService().create(
        serial_number="TIRE-MNL1", brand="Bridgestone", size="185/65R15",
        tire_type="RADIAL", branch_id=manila.id)
    cebu_tire = TireService().create(
        serial_number="TIRE-CEB1", brand="Michelin", size="195/65R15",
        tire_type="RADIAL", branch_id=cebu.id)

    manila_battery = BatteryService().create(
        serial_number="BATT-MNL1", brand="Motolite", branch_id=manila.id)
    cebu_battery = BatteryService().create(
        serial_number="BATT-CEB1", brand="Century", branch_id=cebu.id)

    manila_user = User(username="tirescope_manila", email="tirescope_manila@x.com",
                       password_hash="x")
    cebu_user = User(username="tirescope_cebu", email="tirescope_cebu@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([manila_user, cebu_user])
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)
    UserOrgScopeService().assign(cebu_user.id, scope_type="BRANCH",
                                branch_id=cebu.id)

    return (manila, cebu, manila_tire, cebu_tire, manila_battery,
           cebu_battery, manila_user, cebu_user)


def test_manila_user_does_not_see_cebu_tire(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    visible = TireService().list(user=manila_user)
    visible_ids = {t.id for t in visible}
    assert manila_tire.id in visible_ids
    assert cebu_tire.id not in visible_ids


def test_manila_user_does_not_see_cebu_battery(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    visible = BatteryService().list(user=manila_user)
    visible_ids = {b.id for b in visible}
    assert manila_battery.id in visible_ids
    assert cebu_battery.id not in visible_ids


def test_no_scope_user_sees_all_tires(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    unscoped = User(username="tirescope_unscoped",
                    email="tirescope_unscoped@x.com", password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    visible = TireService().list(user=unscoped)
    visible_ids = {t.id for t in visible}
    assert manila_tire.id in visible_ids
    assert cebu_tire.id in visible_ids


def test_get_visible_blocks_out_of_scope_tire(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    assert TireService().get_visible(cebu_tire.id, manila_user) is None
    assert TireService().get_visible(manila_tire.id, manila_user) is not None


def test_get_visible_blocks_out_of_scope_battery(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    assert BatteryService().get_visible(cebu_battery.id, manila_user) is None
    assert BatteryService().get_visible(manila_battery.id, manila_user) is not None


def test_list_without_user_arg_unchanged(db, env):
    (manila, cebu, manila_tire, cebu_tire, manila_battery, cebu_battery,
     manila_user, cebu_user) = env
    visible = TireService().list()
    visible_ids = {t.id for t in visible}
    assert manila_tire.id in visible_ids
    assert cebu_tire.id in visible_ids
