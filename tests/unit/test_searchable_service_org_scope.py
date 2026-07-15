import pytest

from app.core.search.searchable_service import SearchableService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


class _VehicleSearchServiceForTest(SearchableService):
    model = Vehicle
    search_fields = ["plate_number", "conduction_number", "brand", "model"]

    def label(self, obj):
        return obj.conduction_number or obj.plate_number


@pytest.fixture()
def env(db):
    manila = BranchService().create(code="BR-SEARCHSCOPE-MNL", name="Manila SearchScope")
    cebu = BranchService().create(code="BR-SEARCHSCOPE-CEB", name="Cebu SearchScope")
    vt = VehicleTypeService().create(code="LV-SEARCHSCOPE", name="Light", category="LIGHT")

    manila_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=manila.id, conduction_number="SEARCHSCOPE-MNL-1")
    cebu_vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Honda", model="City", year=2024,
        branch_id=cebu.id, conduction_number="SEARCHSCOPE-CEB-1")

    manila_user = User(username="searchscope_manila", email="searchscope_manila@x.com",
                       password_hash="x")
    from app.extensions import db as _db
    _db.session.add(manila_user)
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)

    return manila, cebu, manila_vehicle, cebu_vehicle, manila_user


def test_search_respects_org_scope_when_user_passed(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=manila_user)
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_search_unrestricted_without_user(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search()
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id in ids


def test_to_select2_response_respects_org_scope(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    resp = svc.to_select2_response(user=manila_user)
    ids = {r["id"] for r in resp["results"]}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_to_table_response_respects_org_scope(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    svc = _VehicleSearchServiceForTest()
    resp = svc.to_table_response(user=manila_user)
    ids = {r["id"] for r in resp["rows"]}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id not in ids


def test_unscoped_user_sees_everything(db, env):
    manila, cebu, manila_vehicle, cebu_vehicle, manila_user = env
    unscoped = User(username="searchscope_unscoped", email="searchscope_unscoped@x.com",
                    password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    svc = _VehicleSearchServiceForTest()
    items, total = svc.search(user=unscoped)
    ids = {v.id for v in items}
    assert manila_vehicle.id in ids
    assert cebu_vehicle.id in ids
