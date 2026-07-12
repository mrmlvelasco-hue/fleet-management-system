import pytest

from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    hq = BranchService().create(code="BR-HQ-VEH", name="Head Office")
    manila = BranchService().create(code="BR-MNL-VEH", name="Manila")
    cebu = BranchService().create(code="BR-CEB-VEH", name="Cebu")
    vt = VehicleTypeService().create(code="LV-VEHSCOPE", name="Light", category="LIGHT")

    hq_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=hq.id, conduction_number="VEHSCOPE-HQ")
    manila_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="HRV", year=2020,
        branch_id=manila.id, conduction_number="VEHSCOPE-MNL")
    cebu_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=cebu.id, conduction_number="VEHSCOPE-CEB")

    manila_user = User(username="vehscope_manila", email="vehscope_manila@x.com",
                       password_hash="x")
    cebu_user = User(username="vehscope_cebu", email="vehscope_cebu@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([manila_user, cebu_user])
    _db.session.commit()

    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)
    UserOrgScopeService().assign(cebu_user.id, scope_type="BRANCH",
                                branch_id=cebu.id)

    return hq, manila, cebu, hq_vehicle, manila_vehicle, cebu_vehicle, manila_user, cebu_user


def test_manila_user_sees_manila_and_hq_but_not_cebu(db, env):
    (hq, manila, cebu, hq_vehicle, manila_vehicle, cebu_vehicle,
     manila_user, cebu_user) = env
    visible = VehicleService().list(user=manila_user)
    visible_ids = {v.id for v in visible}
    assert manila_vehicle.id in visible_ids
    assert cebu_vehicle.id not in visible_ids


def test_cebu_user_does_not_see_manila_vehicle(db, env):
    (hq, manila, cebu, hq_vehicle, manila_vehicle, cebu_vehicle,
     manila_user, cebu_user) = env
    visible = VehicleService().list(user=cebu_user)
    visible_ids = {v.id for v in visible}
    assert manila_vehicle.id not in visible_ids
    assert cebu_vehicle.id in visible_ids


def test_no_scope_user_sees_all_vehicles(db, env):
    (hq, manila, cebu, hq_vehicle, manila_vehicle, cebu_vehicle,
     manila_user, cebu_user) = env
    unscoped = User(username="vehscope_unscoped", email="vehscope_unscoped@x.com",
                    password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    visible = VehicleService().list(user=unscoped)
    visible_ids = {v.id for v in visible}
    assert hq_vehicle.id in visible_ids
    assert manila_vehicle.id in visible_ids
    assert cebu_vehicle.id in visible_ids


def test_list_without_user_arg_unchanged(db, env):
    (hq, manila, cebu, hq_vehicle, manila_vehicle, cebu_vehicle,
     manila_user, cebu_user) = env
    visible = VehicleService().list()
    visible_ids = {v.id for v in visible}
    assert manila_vehicle.id in visible_ids
    assert cebu_vehicle.id in visible_ids
