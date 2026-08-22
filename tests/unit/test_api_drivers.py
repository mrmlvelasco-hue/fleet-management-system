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
