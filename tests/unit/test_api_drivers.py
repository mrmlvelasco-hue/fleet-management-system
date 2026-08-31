"""Driver / Assignee list endpoint.

Written from `docs/parity-driver.md` in the React repo, which was written
from `driver_list.html` — not from any component. That inversion is the
control this project has broken three times.

The list envelope matches the vehicle list exactly (`items` / `total` /
`page` / `page_size` / `pages`) so one frontend scaffold drives both.

Two things carry over from the Jinja list that are easy to read as
decoration and are not:

  * The expired-licence row highlight. `driver_list.html` applies
    `table-warning` to the whole row when `license_expiry < today`, AND
    a red EXPIRED badge in the cell. A driver with a lapsed licence who
    is still assigned to a vehicle is a legal exposure for the client,
    and that row colour is the only place in the system that surfaces it
    at a glance.
  * It is INDEPENDENT of `status`. An ACTIVE driver can hold an expired
    licence, and that combination is precisely the one worth seeing, so
    the flag is computed and returned separately rather than folded into
    the status badge.

Driver status has three values (ACTIVE / INACTIVE / SUSPENDED) against
Vehicle's four different ones. SUSPENDED is driver-only and matters — a
suspended driver must not read as assignable.
"""
import json
from datetime import date, timedelta

import pytest

from app.modules.system_admin.models import SystemParameter

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.master_data.org.service import BranchService
from app.modules.user_management.models import Permission, Role, User


def _driver(db, employee_number, first, last, **kw):
    """Built from the model directly. DriverService.create requires a
    photo upload and full licence details, both real rules and neither
    what a list test is about."""
    from app.modules.master_data.driver.models import Driver
    row = Driver(person_id=f"PID-{employee_number}",
                 employee_number=employee_number, first_name=first,
                 last_name=last,
                 assignee_type=kw.pop("assignee_type", "DRIVER"),
                 status=kw.pop("status", "ACTIVE"),
                 is_active=kw.pop("is_active", True), **kw)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture()
def drv_env(db):
    sync_permissions()
    db.session.commit()

    role = Role(name="Driver Viewer")
    role.permissions = Permission.query.filter(
        Permission.code == "driver.view").all()
    user = User(username="drvuser", email="d@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    nobody_role = Role(name="No Driver Access")
    nobody = User(username="nodrv", email="nd@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    nobody.roles = [nobody_role]
    db.session.add_all([role, user, nobody_role, nobody])

    manila = BranchService().create(code="BR-DM", name="Manila")
    cebu = BranchService().create(code="BR-DC", name="Cebu")
    db.session.commit()

    _driver(db, "EMP-001", "Juan", "Dela Cruz", branch_id=manila.id,
            license_number="N01-1111", license_type="PROFESSIONAL",
            license_expiry=date.today() + timedelta(days=365))
    _driver(db, "EMP-002", "Maria", "Santos", branch_id=cebu.id,
            license_number="N02-2222", license_type="NON_PROFESSIONAL",
            license_expiry=date.today() - timedelta(days=10))
    _driver(db, "EMP-003", "Pedro", "Reyes", branch_id=manila.id,
            assignee_type="CONSULTANT", status="SUSPENDED",
            business_name="Reyes Consulting")
    return {"user": user, "manila": manila, "cebu": cebu}


def _token(client, username="drvuser"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token=None):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


# ── Auth ────────────────────────────────────────────────────────────────────

def test_list_rejects_anonymous(db, client, drv_env):
    assert client.get("/api/v1/drivers").status_code == 401


def test_list_requires_driver_view(db, client, drv_env):
    status, _ = _get(client, "/api/v1/drivers", _token(client, "nodrv"))
    assert status == 403


# ── Envelope ────────────────────────────────────────────────────────────────

def test_envelope_matches_the_vehicle_list(db, client, drv_env):
    """Same shape, so one scaffold drives both. Diverging here means a
    second frontend list implementation, which is the thing the envelope
    exists to prevent."""
    status, body = _get(client, "/api/v1/drivers", _token(client))
    assert status == 200
    assert set(body) >= {"items", "total", "page", "page_size", "pages"}
    assert body["total"] == 3


# ── Columns from the audit ──────────────────────────────────────────────────

def test_row_carries_every_column_the_jinja_list_shows(db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers?q=EMP-001", _token(client))
    row = body["items"][0]
    for field in ("employee_number", "display_name", "license_number",
                  "license_expiry", "license_type", "branch", "status"):
        assert field in row, f"{field} missing from the row"


def test_display_name_uses_the_middle_initial(db, client, drv_env):
    """'Last, First M.' -- the Jinja list renders the middle name as an
    INITIAL with a full stop, not in full. Assembled server-side so both
    apps show one name format."""
    # branch_id is NOT NULL on drivers -- a driver always belongs to a
    # branch, unlike a vehicle, which may sit unassigned.
    _driver(db, "EMP-004", "Ana", "Lopez", middle_name="Bautista",
            branch_id=drv_env["manila"].id)
    _status, body = _get(client, "/api/v1/drivers?q=EMP-004", _token(client))
    assert body["items"][0]["display_name"] == "Lopez, Ana B."


def test_display_name_omits_the_initial_when_there_is_no_middle_name(
        db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers?q=EMP-001", _token(client))
    assert body["items"][0]["display_name"] == "Dela Cruz, Juan"


# ── The expired-licence flag ────────────────────────────────────────────────

def test_expired_licence_is_flagged(db, client, drv_env):
    """The row highlight and the EXPIRED badge both come from this.
    Computed server-side: 'expired' is a comparison against TODAY, and
    doing it in the browser makes it depend on the client's clock."""
    _status, body = _get(client, "/api/v1/drivers?q=EMP-002", _token(client))
    assert body["items"][0]["license_expired"] is True


def test_a_current_licence_is_not_flagged(db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers?q=EMP-001", _token(client))
    assert body["items"][0]["license_expired"] is False


def test_a_driver_with_no_licence_recorded_is_not_flagged_as_expired(
        db, client, drv_env):
    """A consultant has no licence at all. Flagging that as EXPIRED
    would put a red badge on every non-driver in the list and train
    people to ignore the one that matters."""
    _status, body = _get(client, "/api/v1/drivers?q=EMP-003", _token(client))
    assert body["items"][0]["license_expired"] is False


def test_expiry_is_independent_of_status(db, client, drv_env):
    """An ACTIVE driver holding an expired licence is exactly the
    combination worth seeing, so the flag must not be folded into the
    status badge."""
    _status, body = _get(client, "/api/v1/drivers?q=EMP-002", _token(client))
    row = body["items"][0]
    assert row["status"] == "ACTIVE"
    assert row["license_expired"] is True


# ── Filtering ───────────────────────────────────────────────────────────────

def test_search_matches_number_and_either_name(db, client, drv_env):
    for term, expected in (("Santos", "EMP-002"), ("Maria", "EMP-002"),
                           ("EMP-001", "EMP-001")):
        _status, body = _get(client, f"/api/v1/drivers?q={term}",
                             _token(client))
        assert [d["employee_number"] for d in body["items"]] == [expected]


def test_filter_by_branch(db, client, drv_env):
    _status, body = _get(
        client, f"/api/v1/drivers?branch_id={drv_env['cebu'].id}",
        _token(client))
    assert [d["employee_number"] for d in body["items"]] == ["EMP-002"]


def test_filter_by_assignee_type(db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers?assignee_type=CONSULTANT",
                         _token(client))
    assert [d["employee_number"] for d in body["items"]] == ["EMP-003"]


def test_filter_by_status_includes_suspended(db, client, drv_env):
    """SUSPENDED is driver-only and has no vehicle equivalent. A
    suspended driver must be findable, and must not read as assignable."""
    _status, body = _get(client, "/api/v1/drivers?status=SUSPENDED",
                         _token(client))
    assert [d["employee_number"] for d in body["items"]] == ["EMP-003"]


def test_filter_by_expiring_licence(db, client, drv_env):
    """DriverService.get_expiring_licenses(days=30) exists and has NO UI
    in Flask at all. Surfacing it is a React addition, and the one most
    likely to prevent a lapsed-licence driver being dispatched."""
    _status, body = _get(client, "/api/v1/drivers?license_expiring=1",
                         _token(client))
    numbers = [d["employee_number"] for d in body["items"]]
    assert "EMP-002" in numbers
    assert "EMP-001" not in numbers


def test_unknown_status_is_rejected(db, client, drv_env):
    """Silently ignoring a typo would list every driver under a filter
    the caller believes is applied."""
    status, _ = _get(client, "/api/v1/drivers?status=NONSENSE",
                     _token(client))
    assert status == 400


def test_non_integer_branch_is_rejected(db, client, drv_env):
    status, _ = _get(client, "/api/v1/drivers?branch_id=abc", _token(client))
    assert status == 400


# ── Sorting and paging ──────────────────────────────────────────────────────

def test_default_sort_is_by_name(db, client, drv_env):
    """DriverService.list orders by last_name, first_name. A roster in
    insertion order is unusable for finding a person."""
    _status, body = _get(client, "/api/v1/drivers", _token(client))
    assert [d["employee_number"] for d in body["items"]] == [
        "EMP-001", "EMP-003", "EMP-002"]


def test_paging_reports_the_full_total(db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers?page=1&page_size=2",
                         _token(client))
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["pages"] == 2


# ── Scoping ─────────────────────────────────────────────────────────────────

def test_list_respects_org_scope(db, client, drv_env):
    """DriverService.list(user=...) owns this. A roster is a staff list;
    a branch-scoped user must not see another branch's."""
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    UserOrgScopeService().assign(drv_env["user"].id, scope_type="BRANCH",
                                 branch_id=drv_env["cebu"].id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/drivers", _token(client))
    numbers = [d["employee_number"] for d in body["items"]]
    assert numbers == ["EMP-002"]


def test_soft_deleted_drivers_are_listed_as_flask_does(db, client, drv_env):
    """`driver_list` passes include_inactive=True (routes.py 1337).

    Matched deliberately, and tested because a mutation check found this
    had NO coverage -- flipping it to False left all 20 tests green.

    It reads like a bug (why list deleted records?) and is not: a driver
    record is a person's employment history, and `deactivate` is how
    someone who has left is retired. Hiding them would make a former
    employee's trip tickets and vehicle assignments trace back to a
    driver who appears not to exist. The Status column is what
    distinguishes them, which is why the list has one.

    So this must not be "tidied up" to include_inactive=False without
    changing Flask too.
    """
    from app.modules.master_data.driver.service import DriverService

    gone = _driver(db, "EMP-005", "Former", "Employee",
                   branch_id=drv_env["manila"].id)
    DriverService().deactivate(gone.id)
    db.session.commit()

    _status, body = _get(client, "/api/v1/drivers?q=EMP-005", _token(client))
    assert [d["employee_number"] for d in body["items"]] == ["EMP-005"]


def test_inactive_status_is_filterable(db, client, drv_env):
    """Status is separate from soft-deletion: a driver can be INACTIVE
    while their record is live."""
    _driver(db, "EMP-006", "On", "Leave", branch_id=drv_env["manila"].id,
            status="INACTIVE")
    _status, body = _get(client, "/api/v1/drivers?status=INACTIVE",
                         _token(client))
    assert [d["employee_number"] for d in body["items"]] == ["EMP-006"]


# ── Summary ─────────────────────────────────────────────────────────────────

def test_summary_counts_the_roster(db, client, drv_env):
    _status, body = _get(client, "/api/v1/drivers/summary", _token(client))
    assert body["total"] == 3
    assert body["active"] == 2


def test_summary_separates_expiring_from_expired(db, client, drv_env):
    """Two different facts needing two different responses: renew soon,
    versus stop dispatching this person now. One combined count would
    hide the urgent case inside the routine one."""
    _driver(db, "EMP-010", "Soon", "Expiring",
            branch_id=drv_env["manila"].id,
            license_number="N10", license_type="PROFESSIONAL",
            license_expiry=date.today() + timedelta(days=10))
    _status, body = _get(client, "/api/v1/drivers/summary", _token(client))
    assert body["license_expired"] == 1      # EMP-002, lapsed
    assert body["license_expiring"] == 1     # EMP-010, within 30 days


def test_summary_ignores_drivers_with_no_licence(db, client, drv_env):
    """The consultant has none. Counting them as expiring would make the
    chip permanently non-zero and useless."""
    _status, body = _get(client, "/api/v1/drivers/summary", _token(client))
    assert body["license_expiring"] == 0


def test_summary_respects_org_scope(db, client, drv_env):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    UserOrgScopeService().assign(drv_env["user"].id, scope_type="BRANCH",
                                 branch_id=drv_env["manila"].id)
    db.session.commit()
    _status, body = _get(client, "/api/v1/drivers/summary", _token(client))
    # EMP-002 (Cebu, the expired one) is out of scope.
    assert body["total"] == 2
    assert body["license_expired"] == 0


def test_summary_requires_driver_view(db, client, drv_env):
    status, _ = _get(client, "/api/v1/drivers/summary", _token(client, "nodrv"))
    assert status == 403


# ── Export ──────────────────────────────────────────────────────────────────

def _plates_or_numbers(response):
    from io import BytesIO
    from openpyxl import load_workbook
    ws = load_workbook(BytesIO(response.data)).active
    header_row, column = None, None
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        values = [c.value for c in row]
        if "Employee No." in values:
            header_row, column = row[0].row, values.index("Employee No.") + 1
            break
    assert header_row, "no header row in the exported workbook"
    return [ws.cell(r, column).value
            for r in range(header_row + 1, ws.max_row + 1)
            if ws.cell(r, column).value]


def test_export_respects_the_active_filter(db, client, drv_env):
    """Same divergence from Flask as the vehicle export, for the same
    reason: exporting the whole roster while the user is looking at one
    branch is what gets noticed in front of a client."""
    r = client.get(
        f"/api/v1/drivers/export.xlsx?branch_id={drv_env['cebu'].id}",
        headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200
    assert _plates_or_numbers(r) == ["EMP-002"]


def test_export_is_not_capped_at_a_page(db, client, drv_env):
    for i in range(60):
        _driver(db, f"BULK-{i:03d}", "Bulk", f"Person{i}",
                branch_id=drv_env["manila"].id)
    r = client.get("/api/v1/drivers/export.xlsx?q=BULK",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert len(_plates_or_numbers(r)) == 60


def test_export_requires_driver_view(db, client, drv_env):
    r = client.get("/api/v1/drivers/export.xlsx",
                   headers={"Authorization": f"Bearer {_token(client, 'nodrv')}"})
    assert r.status_code == 403


def test_export_marks_expired_licences(db, client, drv_env):
    """The spreadsheet is what gets forwarded. A row that reads as
    routine on paper while the screen showed it red is the difference
    that matters."""
    from io import BytesIO
    from openpyxl import load_workbook
    r = client.get("/api/v1/drivers/export.xlsx?q=EMP-002",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    ws = load_workbook(BytesIO(r.data)).active
    assert any("EXPIRED" in str(c.value)
               for row in ws.iter_rows() for c in row)


# ── Detail ──────────────────────────────────────────────────────────────────

def _detail(client, driver_id, token=None):
    r = client.get(f"/api/v1/drivers/{driver_id}",
                   headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_detail_rejects_anonymous(db, client, drv_env):
    assert client.get("/api/v1/drivers/1").status_code == 401


def test_detail_404s_for_a_driver_outside_scope(db, client, drv_env):
    """Same 404 as a missing record, so the endpoint cannot be used to
    discover which ids exist."""
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.modules.master_data.driver.models import Driver

    cebu_driver = Driver.query.filter_by(employee_number="EMP-002").first()
    UserOrgScopeService().assign(drv_env["user"].id, scope_type="BRANCH",
                                 branch_id=drv_env["manila"].id)
    db.session.commit()
    status, _ = _detail(client, cebu_driver.id, _token(client))
    assert status == 404


def test_detail_carries_every_field_the_jinja_screen_shows(db, client,
                                                           drv_env):
    """Transcribed from docs/parity-driver.md §7, which was transcribed
    from driver_detail.html. Not from the model -- it has 35 columns and
    the screen shows a specific subset under specific headings."""
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-001").first()
    _status, body = _detail(client, d.id, _token(client))

    for field in (
            # Basic
            "person_id", "employee_number", "full_name", "first_name",
            "middle_name", "last_name", "suffix", "nickname",
            "assignee_type", "status",
            # Organization
            "branch", "department", "section", "position", "job_title",
            "cost_center", "employment_status", "employment_type",
            # License
            "license_number", "license_type", "license_expiry",
            "license_expired",
            # Contact
            "phone", "office_number", "home_number", "email",
            "complete_address",
            # Business
            "business_name", "business_contact_no", "business_address",
            # Emergency
            "emergency_contact_person", "emergency_contact_number",
            "emergency_contacts",
            # Assignment
            "assigned_vehicles"):
        assert field in body, f"field missing from detail payload: {field}"


def test_full_name_uses_the_models_own_format(db, client, drv_env):
    """'First M. Last Suffix' -- the model's full_name property, not a
    string rebuilt here. The LIST uses a different format ('Last, First
    M.') and both are correct for their screen; what must not happen is
    a third format invented in the API."""
    _driver(db, "EMP-020", "Ana", "Lopez", branch_id=drv_env["manila"].id,
            middle_name="Bautista", suffix="Jr.")
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-020").first()
    _status, body = _detail(client, d.id, _token(client))
    assert body["full_name"] == "Ana B. Lopez Jr."


def test_detail_flags_an_expired_licence(db, client, drv_env):
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-002").first()
    _status, body = _detail(client, d.id, _token(client))
    assert body["license_expired"] is True


def test_section_visibility_is_decided_server_side(db, client, drv_env):
    """driver_detail.html decides these in Jinja, unlike the FORM which
    decides the same thing in jQuery. The detail screen is the more
    honest implementation of the rule, so the API follows it -- and the
    client is then told which sections apply rather than re-deriving
    the rule and drifting from it."""
    from app.modules.master_data.driver.models import Driver

    driver = Driver.query.filter_by(employee_number="EMP-001").first()
    _status, body = _detail(client, driver.id, _token(client))
    assert body["show_license"] is True
    assert body["show_business"] is False

    consultant = Driver.query.filter_by(employee_number="EMP-003").first()
    _status, body = _detail(client, consultant.id, _token(client))
    assert body["show_license"] is False
    assert body["show_business"] is True


def test_emergency_contacts_are_listed(db, client, drv_env):
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.driver.service import (
        EmergencyContactService)

    d = Driver.query.filter_by(employee_number="EMP-001").first()
    # person_record_id is an integer FK -> drivers.id, NOT the person_id
    # business key ("PID-2026-0001"). Flask's driver_detail route filters
    # EmergencyContact by `did` (drivers.id), so the API matches it. SQLite
    # accepted the string silently, which is why this only surfaced as an
    # empty list rather than an integrity error.
    EmergencyContactService().create(
        person_record_id=d.id, contact_name="Rosa Dela Cruz",
        relationship_type="Spouse", contact_number="0917-000-0000")
    db.session.commit()

    _status, body = _detail(client, d.id, _token(client))
    assert body["emergency_contacts"][0]["contact_name"] == "Rosa Dela Cruz"
    assert body["emergency_contacts"][0]["relationship_type"] == "Spouse"


def test_assigned_vehicles_are_listed(db, client, drv_env):
    """The only place in the system that answers "what is this person
    driving" -- and what makes the expired-licence flag actionable, since
    an expired licence matters precisely because a vehicle is attached
    to it."""
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.vehicle.service import VehicleService

    d = Driver.query.filter_by(employee_number="EMP-001").first()
    vt = VehicleTypeService().create(code="LV-DRV", name="Light",
                                     category="LIGHT")
    # strict=False: strict validates the brand against Vehicle Brand
    # master data, which this fixture has no reason to seed. The rule is
    # real and covered by the vehicle tests; it is not what this test is
    # about.
    VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=drv_env["manila"].id, plate_number="DRV-1111",
        assigned_driver_id=d.id)
    db.session.commit()

    _status, body = _detail(client, d.id, _token(client))
    assert len(body["assigned_vehicles"]) == 1
    v = body["assigned_vehicles"][0]
    assert v["plate_number"] == "DRV-1111"
    # The id is what makes it a LINK back to vehicle detail, which is
    # the whole value of the section.
    assert "id" in v


def test_a_driver_with_no_vehicle_returns_an_empty_list(db, client, drv_env):
    """Empty list, not absent. The client renders "none assigned" from
    an empty list; a missing key is indistinguishable from a failed
    lookup."""
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-003").first()
    _status, body = _detail(client, d.id, _token(client))
    assert body["assigned_vehicles"] == []


# ── Photo ───────────────────────────────────────────────────────────────────

def test_detail_carries_the_photo_attachment_id(db, client, drv_env):
    """driver_print.html renders the photo; driver_detail.html does not.

    The photo is captured on the form, printed on the profile, and
    required at creation precisely because it appears on the Vehicle
    Assignment Memo and the Issuance / Receiving Checklist. A screen
    showing everything about a person except their photograph, while the
    printout of that same screen includes it, is a gap rather than a
    decision -- confirmed by the print audit and agreed with the client.

    The ID rather than a URL: the client builds the download URL from
    the same attachment endpoint it already uses, so there is one
    definition of where an attachment lives.
    """
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-001").first()
    d.photo_attachment_id = 4321
    db.session.commit()

    _status, body = _detail(client, d.id, _token(client))
    assert body["photo_attachment_id"] == 4321


def test_a_driver_with_no_photo_returns_null_not_zero(db, client, drv_env):
    """Legacy records migrated with REQUIRE_ASSIGNEE_PHOTO off have
    none. Null renders a placeholder; 0 would be a valid-looking
    attachment id that 404s on fetch."""
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-003").first()
    _status, body = _detail(client, d.id, _token(client))
    assert body["photo_attachment_id"] is None


def test_the_list_row_says_whether_a_photo_exists(db, client, drv_env):
    """Not the id -- the list does not render photos. But with the
    photo requirement waived for migration, "which records still need a
    photograph" is a question someone will have to answer, and the list
    is where they would answer it."""
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-001").first()
    d.photo_attachment_id = 4321
    db.session.commit()

    _status, body = _get(client, "/api/v1/drivers?q=EMP-001", _token(client))
    assert body["items"][0]["has_photo"] is True

    _status, body = _get(client, "/api/v1/drivers?q=EMP-003", _token(client))
    assert body["items"][0]["has_photo"] is False


# ── Write ───────────────────────────────────────────────────────────────────

def _writer_token(client, db, drv_env):
    """A user holding driver.create and driver.update."""
    from app.core.security.password import hash_password
    from app.modules.user_management.models import Permission, Role, User

    role = Role(name="Driver Editor")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["driver.view", "driver.create",
                             "driver.update"])).all()
    u = User(username="drvwriter", email="dw@e.com",
             password_hash=hash_password("secret123"), is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    db.session.commit()
    return _token(client, "drvwriter")


def _waive_photo(db):
    """These tests exercise the FIELD handling, not the photo rule. The
    rule itself has its own suite (test_driver_creation_parameters.py);
    requiring a file in every one of these would make them all about
    multipart encoding instead."""
    row = SystemParameter.query.filter_by(
        code="REQUIRE_ASSIGNEE_PHOTO").first()
    if row is None:
        row = SystemParameter(code="REQUIRE_ASSIGNEE_PHOTO", value="NO",
                              data_type="BOOLEAN", group_name="DRIVER",
                              description="Migration switch", is_active=True)
        db.session.add(row)
    else:
        row.value = "NO"
    db.session.commit()


def _post(client, token, payload):
    r = client.post("/api/v1/drivers", json=payload,
                    headers={"Authorization": f"Bearer {token}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_create_requires_the_create_permission(db, client, drv_env):
    """driver.view is not enough. Read-only users must not enrol staff."""
    status, _ = _post(client, _token(client), {"first_name": "X"})
    assert status == 403


def test_create_rejects_anonymous(db, client, drv_env):
    assert client.post("/api/v1/drivers", json={}).status_code == 401


def test_create_returns_the_new_driver(db, client, drv_env):
    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    status, body = _post(client, t, {
        "employee_number": "NEW-001", "first_name": "New", "last_name": "Hire",
        "branch_id": drv_env["manila"].id, "assignee_type": "CONSULTANT",
    })
    assert status == 201, body
    assert body["employee_number"] == "NEW-001"
    # The generated Person ID comes back, because it is what the record
    # is known by afterwards and the client cannot derive it.
    assert body["person_id"]


def test_create_coerces_the_licence_expiry_date(db, client, drv_env):
    """license_expiry is a date and JSON has no date type, so it arrives
    as a string. Vehicles shipped a 500 on exactly this before the
    coercion helper was extracted; Drivers uses the same helper rather
    than repeating the mistake."""
    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    status, body = _post(client, t, {
        "employee_number": "NEW-002", "first_name": "Dated",
        "last_name": "Driver", "branch_id": drv_env["manila"].id,
        "assignee_type": "DRIVER", "license_number": "N-DATE",
        "license_type": "PROFESSIONAL", "license_expiry": "2028-05-01",
    })
    assert status == 201, body
    assert body["license_expiry"] == "2028-05-01"


def test_a_malformed_date_is_a_field_error_not_a_500(db, client, drv_env):
    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    status, body = _post(client, t, {
        "employee_number": "NEW-003", "first_name": "Bad",
        "last_name": "Date", "branch_id": drv_env["manila"].id,
        "license_expiry": "01/05/2028",
    })
    assert status == 400, body
    assert "license_expiry" in body["fields"]


def test_a_missing_licence_is_a_field_error_not_a_500(db, client, drv_env):
    """InvalidAssigneeError from the service becomes a 400 naming the
    field, not an unhandled 500. The rule is the service's; only the
    presentation belongs here."""
    _param = SystemParameter(code="REQUIRE_DRIVER_LICENSE", value="YES",
                             data_type="BOOLEAN", group_name="DRIVER",
                             description="x", is_active=True)
    db.session.add(_param)
    db.session.commit()

    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    status, body = _post(client, t, {
        "employee_number": "NEW-004", "first_name": "No",
        "last_name": "Licence", "branch_id": drv_env["manila"].id,
        "assignee_type": "DRIVER",
    })
    assert status == 400, body
    assert "license_number" in body["fields"]


def test_a_duplicate_employee_number_is_a_field_error(db, client, drv_env):
    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    status, body = _post(client, t, {
        "employee_number": "EMP-001", "first_name": "Clash",
        "last_name": "Number", "branch_id": drv_env["manila"].id,
        "assignee_type": "CONSULTANT",
    })
    assert status == 400, body
    assert "employee_number" in body["fields"]


def test_create_ignores_fields_outside_the_allowlist(db, client, drv_env):
    """An allow-list, not **payload. `person_id` is generated by the
    service; letting a client set it would break the traceability the
    generator exists to provide."""
    _waive_photo(db)
    t = _writer_token(client, db, drv_env)
    _status, body = _post(client, t, {
        "employee_number": "NEW-005", "first_name": "Allow",
        "last_name": "List", "branch_id": drv_env["manila"].id,
        "assignee_type": "CONSULTANT", "person_id": "PID-FORGED",
        "id": 9999,
    })
    assert body["person_id"] != "PID-FORGED"
    assert body["id"] != 9999


def test_update_requires_the_update_permission(db, client, drv_env):
    from app.modules.master_data.driver.models import Driver
    d = Driver.query.filter_by(employee_number="EMP-001").first()
    r = client.put(f"/api/v1/drivers/{d.id}", json={"position": "Lead"},
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 403


def test_update_changes_only_what_was_sent(db, client, drv_env):
    """A partial update must not blank the fields it omits. The form
    posts whole sections; a sparse payload from anywhere else must not
    silently clear the rest of the record."""
    from app.modules.master_data.driver.models import Driver
    t = _writer_token(client, db, drv_env)
    d = Driver.query.filter_by(employee_number="EMP-001").first()

    r = client.put(f"/api/v1/drivers/{d.id}", json={"position": "Lead Driver"},
                   headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 200, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["position"] == "Lead Driver"
    assert body["license_number"] == "N01-1111"


def test_update_404s_outside_scope(db, client, drv_env):
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    from app.modules.master_data.driver.models import Driver

    t = _writer_token(client, db, drv_env)
    cebu = Driver.query.filter_by(employee_number="EMP-002").first()
    from app.modules.user_management.models import User
    writer = User.query.filter_by(username="drvwriter").first()
    UserOrgScopeService().assign(writer.id, scope_type="BRANCH",
                                 branch_id=drv_env["manila"].id)
    db.session.commit()

    r = client.put(f"/api/v1/drivers/{cebu.id}", json={"position": "X"},
                   headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 404


def test_create_accepts_multipart_with_a_photo(db, client, drv_env):
    """The path that matters on a normally-configured install.

    REQUIRE_ASSIGNEE_PHOTO defaults to strict and a photo can only
    arrive as a file, so a JSON-only endpoint could never create a
    driver once migration is over -- it would break at exactly the
    moment the client stopped expecting breakage.
    """
    import io

    _param = SystemParameter.query.filter_by(
        code="REQUIRE_ASSIGNEE_PHOTO").first()
    if _param:
        _param.value = "YES"
        db.session.commit()

    t = _writer_token(client, db, drv_env)
    r = client.post(
        "/api/v1/drivers",
        data={
            "employee_number": "MP-001", "first_name": "Multi",
            "last_name": "Part", "branch_id": str(drv_env["manila"].id),
            "assignee_type": "CONSULTANT",
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "id.png"),
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["photo_attachment_id"] is not None


def test_multipart_strings_are_still_coerced(db, client, drv_env):
    """Every multipart value is a string, including branch_id and
    license_expiry. Without coercion these reach the model as text and
    reproduce the fault vehicles had."""
    import io

    t = _writer_token(client, db, drv_env)
    r = client.post(
        "/api/v1/drivers",
        data={
            "employee_number": "MP-002", "first_name": "Typed",
            "last_name": "Fields", "branch_id": str(drv_env["manila"].id),
            "assignee_type": "DRIVER", "license_number": "N-MP2",
            "license_type": "PROFESSIONAL", "license_expiry": "2029-03-15",
            "photo": (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "id.png"),
        },
        content_type="multipart/form-data",
        headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["license_expiry"] == "2029-03-15"
    assert body["branch_id"] == drv_env["manila"].id
