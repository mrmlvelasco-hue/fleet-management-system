import pytest

from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.org.service import BranchService, BusinessUnitService
from app.modules.user_management.models import User
from app.modules.user_management.org_scope_service import UserOrgScopeService


@pytest.fixture()
def env(db):
    laguna = BranchService().create(code="BR-LAG-VEN", name="Laguna")
    manila = BranchService().create(code="BR-MNL-VEN", name="Manila")
    cebu = BranchService().create(code="BR-CEB-VEN", name="Cebu")

    # A shop that legitimately serves two nearby branches
    shared_shop = VendorService().create(code="VEN-SHARED", name="Laguna-Manila Shop")
    VendorService().assign_branches(shared_shop.id, [laguna.id, manila.id])

    # A nationwide supplier with no branch restriction at all
    nationwide = VendorService().create(code="VEN-NATIONWIDE", name="Nationwide Parts Co")

    # A vendor exclusive to Cebu only
    cebu_only = VendorService().create(code="VEN-CEBUONLY", name="Cebu Only Shop")
    VendorService().assign_branches(cebu_only.id, [cebu.id])

    manila_user = User(username="venscope_manila", email="venscope_manila@x.com",
                       password_hash="x")
    cebu_user = User(username="venscope_cebu", email="venscope_cebu@x.com",
                     password_hash="x")
    from app.extensions import db as _db
    _db.session.add_all([manila_user, cebu_user])
    _db.session.commit()
    UserOrgScopeService().assign(manila_user.id, scope_type="BRANCH",
                                branch_id=manila.id)
    UserOrgScopeService().assign(cebu_user.id, scope_type="BRANCH",
                                branch_id=cebu.id)

    return (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
           manila_user, cebu_user)


def test_assign_branches_to_vendor(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    assert {b.id for b in shared_shop.branches} == {laguna.id, manila.id}


def test_manila_user_sees_shared_shop_and_nationwide_but_not_cebu_only(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    visible = VendorService().list(user=manila_user)
    visible_ids = {v.id for v in visible}
    assert shared_shop.id in visible_ids
    assert nationwide.id in visible_ids
    assert cebu_only.id not in visible_ids


def test_cebu_user_sees_cebu_only_and_nationwide_but_not_shared_shop(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    visible = VendorService().list(user=cebu_user)
    visible_ids = {v.id for v in visible}
    assert cebu_only.id in visible_ids
    assert nationwide.id in visible_ids
    assert shared_shop.id not in visible_ids


def test_no_scope_user_sees_all_vendors(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    unscoped = User(username="venscope_unscoped", email="venscope_unscoped@x.com",
                    password_hash="x")
    from app.extensions import db as _db
    _db.session.add(unscoped)
    _db.session.commit()
    visible = VendorService().list(user=unscoped)
    visible_ids = {v.id for v in visible}
    assert shared_shop.id in visible_ids
    assert nationwide.id in visible_ids
    assert cebu_only.id in visible_ids


def test_get_visible_respects_multi_branch_assignment(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    assert VendorService().get_visible(shared_shop.id, manila_user) is not None
    assert VendorService().get_visible(cebu_only.id, manila_user) is None


def test_assign_business_units_to_vendor(db, env):
    bu = BusinessUnitService().create(code="BU-VEN", name="Fleet BU")
    vendor = VendorService().create(code="VEN-BU", name="BU Vendor")
    VendorService().assign_business_units(vendor.id, [bu.id])
    assert {b.id for b in vendor.business_units} == {bu.id}


def test_list_without_user_arg_unchanged(db, env):
    (laguna, manila, cebu, shared_shop, nationwide, cebu_only,
     manila_user, cebu_user) = env
    visible = VendorService().list()
    visible_ids = {v.id for v in visible}
    assert shared_shop.id in visible_ids
    assert cebu_only.id in visible_ids
