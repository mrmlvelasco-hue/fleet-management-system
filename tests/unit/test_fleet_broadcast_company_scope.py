"""Batch 8 (2026-10-31): Fleet Broadcast audience by Company.

Michael: "for the broadcast is also possible to select per company" --
alongside the existing ALL_USERS/ROLE/SPECIFIC_USER/BRANCH audiences,
not instead of them. A COMPANY recipient should reach a user whose
own `User.company_id` is set directly, AND a user who has none of
their own but whose Branch resolves to that Company (Stage 3D's
`effective_company_id` rule, reused here rather than re-derived --
the same rule the Vehicle/Vendor Company filters already use).
"""
import json

import pytest

from app.extensions import db
from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import (
    BranchService, RegionService, AreaDistrictService, OrgCompanyService)
from app.modules.system_admin.services.fleet_broadcast_service import (
    FleetBroadcastService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def svc():
    return FleetBroadcastService()


def _user(username, **kw):
    defaults = {"is_active": True}
    defaults.update(kw)
    u = User(username=username, email=f"{username}@e.com",
             password_hash=hash_password("secret123"), **defaults)
    db.session.add(u)
    return u


def _company(code_suffix):
    region = RegionService().create(code=f"RG-FBC{code_suffix}",
                                     name=f"Region FBC {code_suffix}")
    area = AreaDistrictService().create(code=f"AD-FBC{code_suffix}",
                                        name=f"Area FBC {code_suffix}",
                                        region_id=region.id)
    return OrgCompanyService().create(short_name=f"Co FBC {code_suffix}",
                                      region_id=region.id,
                                      area_district_id=area.id)


def _broadcast(admin, recipients, broadcast_no="FB-FBC-COMPANY"):
    return FleetBroadcastService().create(
        user=admin, broadcast_no=broadcast_no,
        broadcast_type="ANNOUNCEMENT", category="GENERAL",
        title="t", message="m", priority="NORMAL", recipients=recipients)


# ── user_matches_broadcast_audience: own company_id ──────────────────

def test_company_matches_a_user_with_that_company_id_directly(app, svc):
    admin = _user("fbcadm1")
    company = _company("1")
    db.session.flush()
    driver = _user("fbcdrv1", company_id=company.id)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}])

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_company_does_not_match_a_user_with_a_different_company_id(app, svc):
    admin = _user("fbcadm2")
    company = _company("2")
    other = _company("2b")
    db.session.flush()
    driver = _user("fbcdrv2", company_id=other.id)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}],
                     broadcast_no="FB-FBC-COMPANY-2")

    assert svc.user_matches_broadcast_audience(row, driver) is False


# ── user_matches_broadcast_audience: inherited via Branch ─────────────

def test_company_matches_a_user_whose_branch_resolves_to_that_company(
        app, svc):
    admin = _user("fbcadm3")
    company = _company("3")
    branch = BranchService().create(code="BR-FBC3", name="FBC Branch 3",
                                     company_id=company.id)
    db.session.flush()
    driver = _user("fbcdrv3", branch_id=branch.id)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}],
                     broadcast_no="FB-FBC-COMPANY-3")

    assert svc.user_matches_broadcast_audience(row, driver) is True


def test_company_does_not_match_a_user_whose_branch_resolves_to_a_different_company(
        app, svc):
    admin = _user("fbcadm4")
    company = _company("4")
    other = _company("4b")
    branch = BranchService().create(code="BR-FBC4", name="FBC Branch 4",
                                     company_id=other.id)
    db.session.flush()
    driver = _user("fbcdrv4", branch_id=branch.id)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}],
                     broadcast_no="FB-FBC-COMPANY-4")

    assert svc.user_matches_broadcast_audience(row, driver) is False


def test_company_recipient_with_own_company_id_wins_over_an_unmapped_branch(
        app, svc):
    """Same precedence rule as effective_company_id elsewhere: the
    record's own company_id wins when set, the branch is only a
    fallback for when it is not."""
    admin = _user("fbcadm5")
    company = _company("5")
    other = _company("5b")
    branch = BranchService().create(code="BR-FBC5", name="FBC Branch 5",
                                     company_id=other.id)
    db.session.flush()
    driver = _user("fbcdrv5", branch_id=branch.id, company_id=company.id)
    db.session.commit()
    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}],
                     broadcast_no="FB-FBC-COMPANY-5")

    assert svc.user_matches_broadcast_audience(row, driver) is True


# ── Validation ──────────────────────────────────────────────────────

def test_company_recipient_requires_company_id(app, svc):
    admin = _user("fbcadm6")
    db.session.commit()
    with pytest.raises(ValueError, match="company_id"):
        _broadcast(admin, [{"recipient_type": "COMPANY"}],
                  broadcast_no="FB-FBC-COMPANY-6")


# ── _resolve_recipients / publish fan-out ─────────────────────────────

def test_publishing_a_company_broadcast_notifies_only_that_companys_users(
        app, svc):
    from app.modules.system_admin.models import InAppNotification

    admin = _user("fbcadm7")
    company = _company("7")
    other = _company("7b")
    branch = BranchService().create(code="BR-FBC7", name="FBC Branch 7",
                                     company_id=company.id)
    db.session.flush()
    in_company_direct = _user("fbcdrv7a", company_id=company.id)
    in_company_via_branch = _user("fbcdrv7b", branch_id=branch.id)
    outside = _user("fbcdrv7c", company_id=other.id)
    db.session.commit()

    row = _broadcast(admin, [{"recipient_type": "COMPANY",
                              "company_id": company.id}],
                     broadcast_no="FB-FBC-COMPANY-7")
    svc.publish(row.id, user=admin)

    notified = {
        n.user_id for n in InAppNotification.query.filter_by(
            event_code="fleet_broadcast", reference_id=row.id).all()
    }
    assert notified == {in_company_direct.id, in_company_via_branch.id}
    assert outside.id not in notified


# ── API layer: company_id round-trips through create + detail ────────

def _api_token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def test_creating_a_company_broadcast_via_the_api_round_trips_company_id(
        db, client):
    sync_permissions()
    db.session.commit()
    role = Role(name="FB Company API Role")
    role.permissions = Permission.query.filter(
        Permission.code.in_(
            ["fleetbroadcast.create", "fleetbroadcast.view"])).all()
    admin = User(username="fbcapi1", email="fbcapi1@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    admin.roles = [role]
    db.session.add_all([role, admin])
    company = _company("api1")
    db.session.commit()

    token = _api_token(client, "fbcapi1")
    r = client.post(
        "/api/v1/admin/fleet-broadcasts",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "broadcast_no": "FB-FBC-COMPANY-API1",
            "broadcast_type": "ANNOUNCEMENT", "category": "GENERAL",
            "title": "t", "message": "m", "priority": "NORMAL",
            "recipients": [{"recipient_type": "COMPANY",
                           "company_id": company.id}],
        })
    assert r.status_code == 201
    body = json.loads(r.get_data(as_text=True))
    assert body["recipients"][0]["company_id"] == company.id
