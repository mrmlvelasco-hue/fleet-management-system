"""Vendor picker + Vendor Information Summary (B23).

Flask's MO, PR and Invoice forms all search vendors (Select2 against
api_search.search_vendors, "Type vendor code or name"). React replaced
that with a plain <select> fed by `/vendors?page_size=200`, which:

  * silently capped what was SELECTABLE at 200 -- vendor 201 looked
    "not enrolled", the same fault /reference/vehicles fixed for plates;
  * required vendor.view, so an initiator who may raise an MO but not
    maintain the vendor master got an EMPTY vendor dropdown.

/reference/vendors already existed for the tire/battery forms (sign-in
only, by design -- see its docstring). Given `q`, it now searches like
the other pickers; without `q` its old full-list shape is unchanged so
those forms keep working.

The summary is new (approved in the B23 mockup): who the vendor is and
how to reach them, shown to anyone on the MO, plus whether it is
assigned to the vehicle's branch so a mismatch can be flagged -- never
blocked (Flask does not filter by branch either).
"""
import json
from datetime import date

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import (
    VendorContactService, VendorService)
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User
from app.modules.user_management.org_scope_service import UserOrgScopeService


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def venv_(db):
    sync_permissions()
    db.session.commit()

    # Deliberately NO vendor.view: an MO initiator, not a vendor admin.
    role = Role(name="MO Initiator B23")
    role.permissions = Permission.query.filter(Permission.code.in_(
        ["maintenanceorder.view", "maintenanceorder.create"])).all()
    user = User(username="b23init", email="b23@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]
    db.session.add_all([role, user])

    muntinlupa = BranchService().create(code="BR-MUN-23", name="Muntinlupa")
    laspinas = BranchService().create(code="BR-LP-23", name="Las Piñas")
    cebu = BranchService().create(code="BR-CEB-23", name="Cebu")
    vt = VehicleTypeService().create(code="LV-23", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="MT-23", name="PMS",
                                         category="PM")
    db.session.commit()
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2024,
        branch_id=muntinlupa.id, plate_number="NAO-917")

    toyota = VendorService().create(
        code="VEN-001", name="ALABANG TOYOTA", vendor_type="SERVICES",
        tin="123-456-789-000", address="Alabang-Zapote Rd", city="Muntinlupa",
        phone="(02) 8807-1234", email="service@alabangtoyota.ph",
        contact_person="Juan Dela Cruz")
    VendorService().assign_branches(toyota.id, [muntinlupa.id, laspinas.id])
    VendorContactService().create(vendor_id=toyota.id,
                                  contact_name="Ana Reyes",
                                  position="Parts", cel_number="0917 555 0101")
    geely = VendorService().create(code="VEN-007", name="Geely Alabang",
                                   vendor_type="BOTH", city="Muntinlupa")
    cebu_shop = VendorService().create(code="VEN-031", name="Cebu Tire Supply",
                                       vendor_type="GOODS", city="Cebu")
    VendorService().assign_branches(cebu_shop.id, [cebu.id])
    gone = VendorService().create(code="VEN-099", name="Alabang Closed Shop")
    VendorService().deactivate(gone.id)
    _ensure_doc_type("MO")
    db.session.commit()
    return {"user": user, "vehicle": vehicle, "toyota": toyota,
            "geely": geely, "cebu_shop": cebu_shop, "gone": gone,
            "muntinlupa": muntinlupa, "cebu": cebu, "mt": mt}


def _token(client, username="b23init"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Search ──────────────────────────────────────────────────────────────────

def test_search_rejects_anonymous(db, client, venv_):
    assert client.get("/api/v1/reference/vendors?q=ala").status_code == 401


def test_an_initiator_without_vendor_view_can_search(db, client, venv_):
    """The empty-dropdown bug: the MO form used /vendors (vendor.view)."""
    status, body = _get(client, "/api/v1/reference/vendors?q=", _token(client))
    assert status == 200
    assert len(body["items"]) >= 3


def test_search_matches_code_and_name_case_insensitively(db, client, venv_):
    t = _token(client)
    _s, by_name = _get(client, "/api/v1/reference/vendors?q=alabang", t)
    assert [v["code"] for v in by_name["items"]] == ["VEN-001", "VEN-007"]
    _s, by_code = _get(client, "/api/v1/reference/vendors?q=ven-031", t)
    assert [v["code"] for v in by_code["items"]] == ["VEN-031"]


def test_search_never_offers_an_inactive_vendor(db, client, venv_):
    _s, body = _get(client, "/api/v1/reference/vendors?q=closed",
                    _token(client))
    assert body["items"] == []


def test_search_item_carries_label_type_and_city(db, client, venv_):
    """Label matches Flask's VendorSearchService ("CODE — Name")."""
    _s, body = _get(client, "/api/v1/reference/vendors?q=VEN-001",
                    _token(client))
    item = body["items"][0]
    assert item["label"] == "VEN-001 — ALABANG TOYOTA"
    assert item["vendor_type"] == "SERVICES"
    assert item["city"] == "Muntinlupa"


def test_search_caps_display_and_says_so(db, client, venv_):
    for i in range(30):
        VendorService().create(code=f"BULK-{i:03d}", name=f"Bulk Shop {i}")
    _s, body = _get(client, "/api/v1/reference/vendors?q=bulk",
                    _token(client))
    assert len(body["items"]) == 25
    assert body["has_more"] is True
    assert body["total"] == 30


def test_a_vendor_beyond_the_old_200_cap_is_findable(db, client, venv_):
    for i in range(210):
        VendorService().create(code=f"AAA-{i:03d}", name=f"Aaa Shop {i}")
    VendorService().create(code="ZZZ-999", name="Zulu Motors")
    _s, body = _get(client, "/api/v1/reference/vendors?q=zulu",
                    _token(client))
    assert [v["code"] for v in body["items"]] == ["ZZZ-999"]


def test_search_follows_vendor_branch_visibility(db, client, venv_):
    """Same rule as the vendor master (VendorService._is_visible): a
    user scoped to Muntinlupa is not offered a Cebu-only vendor."""
    UserOrgScopeService().assign(venv_["user"].id, scope_type="BRANCH",
                                 branch_id=venv_["muntinlupa"].id)
    _s, body = _get(client, "/api/v1/reference/vendors?q=",
                    _token(client))
    codes = {v["code"] for v in body["items"]}
    assert "VEN-031" not in codes
    assert {"VEN-001", "VEN-007"} <= codes


def test_without_q_the_legacy_full_list_shape_is_unchanged(db, client, venv_):
    """Tire / battery / PR forms call /reference/vendors with no q and
    expect every active vendor, {id, code, name}, no has_more."""
    _s, body = _get(client, "/api/v1/reference/vendors", _token(client))
    assert "has_more" not in body
    assert {v["code"] for v in body["items"]} == {
        "VEN-001", "VEN-007", "VEN-031"}
    assert set(body["items"][0]) >= {"id", "code", "name"}


# ── Summary ─────────────────────────────────────────────────────────────────

def test_summary_carries_who_and_how_to_reach_them(db, client, venv_):
    v = venv_["toyota"]
    status, body = _get(client, f"/api/v1/reference/vendors/{v.id}/summary",
                        _token(client))
    assert status == 200
    assert body["code"] == "VEN-001"
    assert body["name"] == "ALABANG TOYOTA"
    assert body["vendor_type"] == "SERVICES"
    assert body["tin"] == "123-456-789-000"
    assert body["contact_person"] == "Juan Dela Cruz"
    assert body["phone"] == "(02) 8807-1234"
    assert body["email"] == "service@alabangtoyota.ph"
    assert body["address"] == "Alabang-Zapote Rd"
    assert body["city"] == "Muntinlupa"
    assert [b["name"] for b in body["branches"]] == ["Las Piñas", "Muntinlupa"]
    assert body["serves_all_branches"] is False
    assert body["contacts"] == [{
        "name": "Ana Reyes", "position": "Parts", "tel": None,
        "cel": "0917 555 0101", "email": None}]


def test_summary_flags_a_vendor_not_assigned_to_the_vehicles_branch(
        db, client, venv_):
    t = _token(client)
    vid = venv_["vehicle"].id
    _s, ok = _get(client, f"/api/v1/reference/vendors/{venv_['toyota'].id}"
                          f"/summary?vehicle_id={vid}", t)
    assert ok["serves_vehicle_branch"] is True
    assert ok["vehicle_branch"] == "Muntinlupa"

    _s, nationwide = _get(client, f"/api/v1/reference/vendors/"
                                  f"{venv_['geely'].id}/summary?vehicle_id={vid}", t)
    assert nationwide["serves_all_branches"] is True
    assert nationwide["serves_vehicle_branch"] is True

    _s, other = _get(client, f"/api/v1/reference/vendors/"
                             f"{venv_['cebu_shop'].id}/summary?vehicle_id={vid}", t)
    assert other["serves_vehicle_branch"] is False


def test_summary_without_a_vehicle_does_not_guess(db, client, venv_):
    _s, body = _get(client, f"/api/v1/reference/vendors/"
                            f"{venv_['cebu_shop'].id}/summary", _token(client))
    assert body["serves_vehicle_branch"] is None


def test_summary_needs_sign_in_only_not_vendor_view(db, client, venv_):
    vid = venv_["toyota"].id
    assert client.get(f"/api/v1/reference/vendors/{vid}/summary").status_code == 401
    status, _b = _get(client, f"/api/v1/reference/vendors/{vid}/summary",
                      _token(client))
    assert status == 200


def test_summary_404s_for_unknown_or_invisible_vendor(db, client, venv_):
    t = _token(client)
    assert _get(client, "/api/v1/reference/vendors/999999/summary", t)[0] == 404
    UserOrgScopeService().assign(venv_["user"].id, scope_type="BRANCH",
                                 branch_id=venv_["muntinlupa"].id)
    assert _get(client, f"/api/v1/reference/vendors/"
                        f"{venv_['cebu_shop'].id}/summary", t)[0] == 404


# ── MO detail carries it ────────────────────────────────────────────────────

def test_mo_detail_carries_the_vendor_summary(db, client, venv_):
    """Approvers open the MO, not the vendor master -- and most of them
    lack vendor.view, so the summary travels inside the MO payload."""
    order = MaintenanceOrderService().create(
        vehicle_id=venv_["vehicle"].id, maintenance_type_id=venv_["mt"].id,
        scheduled_date=date.today(), user=None,
        vendor_id=venv_["cebu_shop"].id)
    db.session.commit()
    _s, body = _get(client, f"/api/v1/maintenance-orders/{order.id}",
                    _token(client))
    s = body["vendor_summary"]
    assert s["code"] == "VEN-031"
    assert s["serves_vehicle_branch"] is False
    assert s["vehicle_branch"] == "Muntinlupa"


def test_mo_detail_with_no_vendor_has_null_summary(db, client, venv_):
    order = MaintenanceOrderService().create(
        vehicle_id=venv_["vehicle"].id, maintenance_type_id=venv_["mt"].id,
        scheduled_date=date.today(), user=None)
    db.session.commit()
    _s, body = _get(client, f"/api/v1/maintenance-orders/{order.id}",
                    _token(client))
    assert body["vendor_summary"] is None


def test_mo_detail_keeps_a_since_deactivated_vendors_summary(db, client, venv_):
    """The order's history must still say who did the work."""
    order = MaintenanceOrderService().create(
        vehicle_id=venv_["vehicle"].id, maintenance_type_id=venv_["mt"].id,
        scheduled_date=date.today(), user=None,
        vendor_id=venv_["geely"].id)
    db.session.commit()
    VendorService().deactivate(venv_["geely"].id)
    _s, body = _get(client, f"/api/v1/maintenance-orders/{order.id}",
                    _token(client))
    assert body["vendor_summary"]["code"] == "VEN-007"
    assert body["vendor_summary"]["is_active"] is False
