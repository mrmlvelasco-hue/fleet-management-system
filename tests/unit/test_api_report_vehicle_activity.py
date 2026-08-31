"""Vehicle Activity History report.

Parity source: report_vehicle_activity_history / ..._export in
master_data/routes.py, VehicleActivityHistoryService. See
docs/parity-report-vehicle-activity.md.

Structurally unlike the other three reports: the "filter" is a list of
specific vehicle ids, not branch/date/status, and there is no
unfiltered "browse everything" mode -- an empty selection is an empty
report, not an error.
"""
import json
from datetime import date, timedelta

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    MaintenanceTypeService, VehicleTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.transactions.maintenance_order.service import (
    MaintenanceOrderService)
from app.modules.user_management.models import Permission, Role, User


def _ensure_doc_type(code):
    from app.modules.document_config.models import DocumentType
    from app.modules.document_config.service import (
        DocumentTypeService, NumberingSchemeService)
    if DocumentType.query.filter_by(code=code).first() is None:
        DocumentTypeService().create(code=code, name=code,
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code=code).first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix=code,
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")


@pytest.fixture()
def env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Activity Report Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "reportvehicleactivity.view").all()
    viewer = User(username="actviewer", email="av@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [role]
    nobody = User(username="actnobody", email="an@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    db.session.add_all([role, viewer, nobody])

    branch = BranchService().create(code="BR-ACT", name="Act Branch")
    vt = VehicleTypeService().create(code="LV-ACT", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code="ACT-MT", name="PMS",
                                         category="PM")
    _ensure_doc_type("MO")

    v1 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2023,
        branch_id=branch.id, conduction_number="ACT-001",
        acquisition_date=date.today() - timedelta(days=400),
        acquisition_cost=900000)
    v2 = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Traviz", year=2022,
        branch_id=branch.id, conduction_number="ACT-002")
    db.session.commit()
    return {"viewer": viewer, "branch": branch, "vt": vt, "mt": mt,
            "v1": v1, "v2": v2}


def _completed_order(env, vehicle, cost=1000, days_ago=5):
    o = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=env["mt"].id,
        scheduled_date=date.today(), user=env["viewer"],
        description="Activity fixture", estimated_cost=cost)
    o.status = "COMPLETED"
    o.actual_cost = cost
    o.completed_date = date.today() - timedelta(days=days_ago)
    from app.extensions import db
    db.session.commit()
    return o


def _token(client, username="actviewer"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, r


def test_rejects_anonymous(db, client, env):
    assert client.get("/api/v1/reports/vehicle-activity").status_code == 401


def test_requires_the_permission(db, client, env):
    status, _ = _get(client,
                     f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}",
                     _token(client, "actnobody"))
    assert status == 403


def test_no_vehicle_ids_is_an_empty_report_not_an_error(db, client, env):
    """There is no unfiltered 'browse everything' mode for this
    report -- an empty selection means nothing was asked for."""
    status, r = _get(client, "/api/v1/reports/vehicle-activity",
                     _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["sections"] == []


def test_one_section_per_selected_vehicle(db, client, env):
    url = (f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}"
           f"&vehicle_ids={env['v2'].id}")
    status, r = _get(client, url, _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    ids = {s["vehicle"]["id"] for s in body["sections"]}
    assert ids == {env["v1"].id, env["v2"].id}


def test_a_nonexistent_id_is_also_silently_dropped(db, client, env):
    status, r = _get(
        client, "/api/v1/reports/vehicle-activity?vehicle_ids=999999",
        _token(client))
    assert status == 200
    assert json.loads(r.get_data(as_text=True))["sections"] == []


def test_an_id_outside_org_scope_is_silently_dropped(db, client, env):
    """get_visible returns None for a vehicle outside scope -- one bad
    id in the selection must not blow up the whole report, and it is
    not the caller's business WHY it is missing (nonexistent vs. out of
    scope look the same from here).

    covers() returns True for a user with no scope rows at all, so this
    only actually exercises the org-scope branch (rather than merely
    the nonexistent-id branch, which get_visible also returns None for)
    once the viewer is explicitly scoped to a DIFFERENT branch than the
    vehicle's own.
    """
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    other_branch = BranchService().create(code="BR-ACT-OTHER", name="Other")
    UserOrgScopeService().assign(env["viewer"].id, scope_type="BRANCH",
                                 branch_id=other_branch.id)
    db.session.commit()

    status, r = _get(
        client, f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}",
        _token(client))
    assert status == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["sections"] == []


def test_section_has_all_three_sub_parts(db, client, env):
    _completed_order(env, env["v1"])
    _status, r = _get(
        client, f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    section = body["sections"][0]
    assert "activity_rows" in section
    assert "utilization" in section
    assert "outlet_history" in section


def test_activity_rows_include_the_completed_order(db, client, env):
    _completed_order(env, env["v1"], cost=1500)
    _status, r = _get(
        client, f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    rows = body["sections"][0]["activity_rows"]
    # cost crosses the wire as a STRING (see _money's docstring): a
    # float turns 1234567.89 into binary rounding, and this figure is
    # read as pesos. Same rule the fuel endpoint already follows.
    assert any(row["cost"] == "1500.00" for row in rows)


def test_activity_rows_include_acquisition_when_dated(db, client, env):
    """v1 has an acquisition_date in the fixture; v2 does not."""
    _status, r = _get(
        client,
        f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}"
        f"&vehicle_ids={env['v2'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    v1_section = next(s for s in body["sections"]
                      if s["vehicle"]["id"] == env["v1"].id)
    v2_section = next(s for s in body["sections"]
                      if s["vehicle"]["id"] == env["v2"].id)
    assert any(r["activity_type"] == "Acquisition"
              for r in v1_section["activity_rows"])
    assert not any(r["activity_type"] == "Acquisition"
                  for r in v2_section["activity_rows"])


def test_utilization_is_derived_from_this_vehicles_own_rows(db, client, env):
    """Not a separate query -- the counts must match what activity_rows
    for THIS vehicle actually contains, not the whole fleet's, and not
    anything else spliced in along the way."""
    _completed_order(env, env["v1"], cost=1000)
    _completed_order(env, env["v2"], cost=99999)  # a different vehicle

    _status, r = _get(
        client, f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v1'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    util = body["sections"][0]["utilization"]
    # Exact, not just "not equal to v2's figure": a weaker assertion
    # (!= 99999) still passes if v2's cost were ever added ON TOP of
    # v1's own total rather than replacing it, since 1000 + 99999 is
    # also != 99999. Only an exact match rules out contamination from
    # any source, not just the one this test names.
    assert util["total_maintenance_cost"] == 1000.0
    assert util["pms_count"] == 1


def test_outlet_history_present_even_with_no_audit_log(db, client, env):
    """A vehicle with no branch-change audit history still gets one
    segment showing its current assignment, per get_outlet_history's
    fallback."""
    _status, r = _get(
        client, f"/api/v1/reports/vehicle-activity?vehicle_ids={env['v2'].id}",
        _token(client))
    body = json.loads(r.get_data(as_text=True))
    assert len(body["sections"][0]["outlet_history"]) >= 1


def test_export_is_an_xlsx_attachment(db, client, env):
    status, r = _get(
        client,
        f"/api/v1/reports/vehicle-activity/export.xlsx?vehicle_ids={env['v1'].id}",
        _token(client))
    assert status == 200
    assert "spreadsheetml" in r.headers["Content-Type"]


def test_export_requires_the_permission(db, client, env):
    status, _ = _get(
        client,
        f"/api/v1/reports/vehicle-activity/export.xlsx?vehicle_ids={env['v1'].id}",
        _token(client, "actnobody"))
    assert status == 403


def test_bad_vehicle_id_is_a_clean_400(db, client, env):
    status, _ = _get(
        client, "/api/v1/reports/vehicle-activity?vehicle_ids=abc",
        _token(client))
    assert status == 400
