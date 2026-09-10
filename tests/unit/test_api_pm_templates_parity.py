"""PM Template / Scope Template API parity with the Jinja screens.

Written from `parity-pm-config.md`, which was written from
`maintenance_config/templates/*.html` and `maintenance_config/routes.py`
— not from the React components. Every assertion below traces to
something the Flask screen renders today and the API could not express.

Five gaps, all of which the React screens need:

  D1  The list payload carries `vehicle_brand_id` but not the brand's
      NAME, so a Make/Model column cannot be rendered without an N+1
      round-trip per row. Same for the model and the variant, which the
      Jinja cell shows in muted text beside them.
  D2  The detail payload omits `scope_templates`, so the "Linked Scope
      Templates" block on the detail screen cannot be built at all.
  D3  No `maintenance_type_id` filter, which the Jinja toolbar has.
  D4  The list materialised EVERY row and sliced in Python.
      `PMScheduleService.list_paginated` — already used by the Jinja
      screen, already eager-loading the four relationships, already
      handling search and the maintenance-type filter — sat unused
      beside it. At the client's 4,626 templates that is thousands of
      ORM objects and four relationship loads per row on every page
      view.
  D5  Export was Jinja-only (`master_data_export`), session-
      authenticated, so a bearer token could not reach it.

Plus two smaller ones found while reading: `effective_date` is on the
model and on the Jinja form but was silently dropped by both the create
and update handlers, and `required_parts` is a scope item column the
Jinja detail table shows but the serializer omitted.
"""
import io
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.modules.user_management.models import Permission, Role, User


PM_PERMS = ("pmschedule.view", "pmschedule.create", "pmschedule.update",
            "pmschedule.delete", "pmscopetemplate.view",
            "pmscopetemplate.create")


@pytest.fixture()
def pm_env(db):
    from app.modules.master_data.reference.service import (
        VehicleTypeService, MaintenanceTypeService)
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    from app.modules.maintenance_config.service import (
        PMScheduleService, PMScopeTemplateService)

    sync_permissions()
    db.session.commit()

    role = Role(name="PM Admin")
    role.permissions = Permission.query.filter(
        Permission.code.in_(PM_PERMS)).all()
    user = User(username="pmadmin", email="pm@e.com",
                password_hash=hash_password("secret123"), is_active=True)
    user.roles = [role]

    nobody = User(username="pmnobody", email="pmn@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    nobody.roles = [Role(name="No PM Access")]

    # Read-only. Needed to prove the create-vs-view split on import: a
    # user with NO permissions is 403 either way, so testing against
    # `pmnobody` alone would pass just as happily against an import
    # endpoint guarded on `.view`. Caught by mutation-testing the guard.
    viewer_role = Role(name="PM Viewer")
    viewer_role.permissions = Permission.query.filter(
        Permission.code.in_(("pmschedule.view", "pmscopetemplate.view"))).all()
    viewer = User(username="pmviewer", email="pmv@e.com",
                  password_hash=hash_password("secret123"), is_active=True)
    viewer.roles = [viewer_role]

    db.session.add_all([role, user, nobody, viewer_role, viewer])
    db.session.commit()

    vt = VehicleTypeService().create(code="LV-P", name="Light Vehicle",
                                     category="LIGHT")
    pms = MaintenanceTypeService().create(code="PMS-PREV", name="Preventive",
                                          category="PREVENTIVE")
    corr = MaintenanceTypeService().create(code="CM-CORR", name="Corrective",
                                           category="CORRECTIVE")
    brand = VehicleBrandService().create(name="Ford")
    model = VehicleModelService().create(brand_id=brand.id, name="Escape")
    db.session.commit()

    sched = PMScheduleService().create(
        maintenance_type_id=pms.id, trigger_mode="HYBRID",
        vehicle_type_id=vt.id, vehicle_brand_id=brand.id,
        vehicle_model_id=model.id, variant="2.0 EcoBoost",
        profile_code="S02-00001", profile_description="Ford Escape 5,000 km",
        interval_km=5000, interval_days=90, priority="MEDIUM",
        work_description_template="5,000 km servicing of pm2 pm3")
    other = PMScheduleService().create(
        maintenance_type_id=corr.id, trigger_mode="KM",
        vehicle_make="Isuzu", vehicle_model="D-Max", interval_km=10000)

    scope = PMScopeTemplateService().create(
        maintenance_type_id=pms.id, name="Ford Escape 5,000 km - Package 2",
        pm_schedule_id=sched.id,
        items=[{"activity_code": "S02-00001-001",
                "activity_description": "Change oil",
                "standard_labor_hours": 1.5,
                "required_parts": "Engine oil 5W-30 x 4L",
                "sort_order": 0},
               {"activity_code": "S02-00001-002",
                "activity_description": "Replace oil filter",
                "sort_order": 1}])
    db.session.commit()
    return {"sched": sched, "other": other, "scope": scope, "pms": pms,
            "corr": corr, "brand": brand, "model": model, "vt": vt}


def _token(client, username="pmadmin"):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True)).get("access_token")


def _get(client, url, token=None):
    r = client.get(url, headers={"Authorization": f"Bearer {token}"})
    body = r.get_data(as_text=True)
    try:
        return r.status_code, json.loads(body)
    except ValueError:
        return r.status_code, body


def _row(body, sid):
    return next(i for i in body["items"] if i["id"] == sid)


# ── D1: the Make / Model column ─────────────────────────────────────────────

def test_list_row_carries_the_brand_and_model_names(db, client, pm_env):
    """The Jinja cell renders `vehicle_brand.name vehicle_model_ref.name`.
    With ids only, React would need one lookup per row to draw one
    column."""
    status, body = _get(client, "/api/v1/pm-templates", _token(client))
    assert status == 200
    row = _row(body, pm_env["sched"].id)
    assert row["vehicle_brand"] == "Ford"
    assert row["vehicle_model_ref"] == "Escape"


def test_list_row_carries_the_variant(db, client, pm_env):
    """Shown in muted text beside the model on the Jinja list."""
    _, body = _get(client, "/api/v1/pm-templates", _token(client))
    assert _row(body, pm_env["sched"].id)["variant"] == "2.0 EcoBoost"


def test_legacy_free_text_row_reports_no_brand_refs(db, client, pm_env):
    """Precedence, not just presence: the Jinja cell falls back to the
    free-text make/model ONLY when the FK pair is absent. A row that
    reported both would render both."""
    _, body = _get(client, "/api/v1/pm-templates", _token(client))
    row = _row(body, pm_env["other"].id)
    assert row["vehicle_brand"] is None
    assert row["vehicle_model_ref"] is None
    assert row["vehicle_make"] == "Isuzu"


# ── D2: Linked Scope Templates on the detail screen ─────────────────────────

def test_detail_carries_linked_scope_templates(db, client, pm_env):
    sid = pm_env["sched"].id
    status, body = _get(client, f"/api/v1/pm-templates/{sid}", _token(client))
    assert status == 200
    assert len(body["scope_templates"]) == 1
    st = body["scope_templates"][0]
    assert st["name"] == "Ford Escape 5,000 km - Package 2"
    assert st["item_count"] == 2


def test_linked_scope_items_are_sorted_and_complete(db, client, pm_env):
    """The Jinja table sorts by sort_order and has a Parts column."""
    sid = pm_env["sched"].id
    _, body = _get(client, f"/api/v1/pm-templates/{sid}", _token(client))
    items = body["scope_templates"][0]["items"]
    assert [i["sort_order"] for i in items] == [0, 1]
    assert items[0]["required_parts"] == "Engine oil 5W-30 x 4L"
    assert items[0]["standard_labor_hours"] == 1.5


def test_list_payload_does_not_carry_scope_templates(db, client, pm_env):
    """Detail-only. Putting them on the list would reintroduce exactly
    the eager activity-row load that the scope list was fixed to avoid."""
    _, body = _get(client, "/api/v1/pm-templates", _token(client))
    assert "scope_templates" not in _row(body, pm_env["sched"].id)


# ── D3: the Maintenance Type filter ─────────────────────────────────────────

def test_list_filters_by_maintenance_type(db, client, pm_env):
    mt = pm_env["pms"].id
    _, body = _get(client, f"/api/v1/pm-templates?maintenance_type_id={mt}",
                   _token(client))
    assert [i["id"] for i in body["items"]] == [pm_env["sched"].id]
    assert body["total"] == 1


def test_filtered_total_reflects_the_filter_not_the_table(db, client, pm_env):
    """The Jinja toolbar prints `N templates (filtered)` from this
    number. A total that ignored the filter would report 4,626 next to
    one visible row."""
    mt = pm_env["corr"].id
    _, body = _get(client, f"/api/v1/pm-templates?maintenance_type_id={mt}",
                   _token(client))
    assert body["total"] == 1
    assert body["items"][0]["id"] == pm_env["other"].id


# ── D4: pagination must happen in SQL ───────────────────────────────────────

def test_list_never_selects_pm_schedules_without_a_limit(db, client, pm_env):
    """The real regression guard.

    Asserting on `len(items)` cannot catch this: slicing in Python
    returns a correct-looking page while having already built every row.
    So watch the SQL instead — any unbounded SELECT against
    pm_schedules means the whole table was materialised again.
    """
    from sqlalchemy import event
    from app.extensions import db as _db

    seen = []

    def record(conn, cursor, statement, params, context, executemany):
        collapsed = " ".join(statement.split()).lower()
        if "from pm_schedules" in collapsed and collapsed.startswith("select"):
            seen.append(collapsed)

    engine = _db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        status, _ = _get(client, "/api/v1/pm-templates?page_size=1",
                         _token(client))
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert status == 200
    unbounded = [s for s in seen
                 if "limit" not in s and "count(" not in s]
    assert not unbounded, (
        "the PM template list materialised every row again; "
        f"unbounded query: {unbounded[:1]}")


def test_paging_returns_distinct_pages(db, client, pm_env):
    t = _token(client)
    _, p1 = _get(client, "/api/v1/pm-templates?page=1&page_size=1", t)
    _, p2 = _get(client, "/api/v1/pm-templates?page=2&page_size=1", t)
    assert p1["total"] == 2 and p1["pages"] == 2
    assert p1["items"][0]["id"] != p2["items"][0]["id"]


def test_search_is_applied_server_side(db, client, pm_env):
    _, body = _get(client, "/api/v1/pm-templates?q=Isuzu", _token(client))
    assert [i["id"] for i in body["items"]] == [pm_env["other"].id]
    assert body["total"] == 1


# ── effective_date round-trip ───────────────────────────────────────────────

def test_create_persists_effective_date(db, client, pm_env):
    """On the model and on the Jinja form; both handlers dropped it."""
    r = client.post("/api/v1/pm-templates",
                    headers={"Authorization": f"Bearer {_token(client)}"},
                    json={"maintenance_type_id": pm_env["pms"].id,
                          "trigger_mode": "KM", "interval_km": 1000,
                          "effective_date": "2026-01-15"})
    assert r.status_code == 201, r.get_data(as_text=True)
    assert json.loads(r.get_data(as_text=True))["effective_date"] == "2026-01-15"


def test_update_persists_effective_date(db, client, pm_env):
    sid = pm_env["sched"].id
    r = client.put(f"/api/v1/pm-templates/{sid}",
                   headers={"Authorization": f"Bearer {_token(client)}"},
                   json={"effective_date": "2026-03-01"})
    assert r.status_code == 200, r.get_data(as_text=True)
    _, body = _get(client, f"/api/v1/pm-templates/{sid}", _token(client))
    assert body["effective_date"] == "2026-03-01"


def test_blank_effective_date_clears_it(db, client, pm_env):
    """A date field the user emptied must actually empty. Treating "" as
    "not supplied" would make the field one-way."""
    sid = pm_env["sched"].id
    hdr = {"Authorization": f"Bearer {_token(client)}"}
    client.put(f"/api/v1/pm-templates/{sid}", headers=hdr,
               json={"effective_date": "2026-03-01"})
    client.put(f"/api/v1/pm-templates/{sid}", headers=hdr,
               json={"effective_date": ""})
    _, body = _get(client, f"/api/v1/pm-templates/{sid}", _token(client))
    assert body["effective_date"] is None


# ── Section 4 policy fields ─────────────────────────────────────────────────

def test_create_persists_the_scheduling_policy_fields(db, client, pm_env):
    """`next_pms_generation` and `next_due_calculation_method` are
    section 4 of the Jinja form and change what the scheduler actually
    does when a vehicle falls due. Both handlers dropped them, so every
    template created through the API silently took the model defaults —
    a form that appears to accept a setting and discards it."""
    r = client.post("/api/v1/pm-templates",
                    headers={"Authorization": f"Bearer {_token(client)}"},
                    json={"maintenance_type_id": pm_env["pms"].id,
                          "trigger_mode": "KM", "interval_km": 2000,
                          "next_pms_generation": "MANUAL",
                          "next_due_calculation_method": "ORIGINAL_SCHEDULE"})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["next_pms_generation"] == "MANUAL"
    assert body["next_due_calculation_method"] == "ORIGINAL_SCHEDULE"


def test_update_persists_the_scheduling_policy_fields(db, client, pm_env):
    sid = pm_env["sched"].id
    r = client.put(f"/api/v1/pm-templates/{sid}",
                   headers={"Authorization": f"Bearer {_token(client)}"},
                   json={"next_pms_generation": "AUTO_MO"})
    assert r.status_code == 200, r.get_data(as_text=True)
    _, body = _get(client, f"/api/v1/pm-templates/{sid}", _token(client))
    assert body["next_pms_generation"] == "AUTO_MO"


def test_unknown_policy_value_is_rejected(db, client, pm_env):
    """These are enumerated in the Jinja select. An arbitrary string
    would reach the scheduler and match none of its branches."""
    r = client.post("/api/v1/pm-templates",
                    headers={"Authorization": f"Bearer {_token(client)}"},
                    json={"maintenance_type_id": pm_env["pms"].id,
                          "trigger_mode": "KM", "interval_km": 2000,
                          "next_pms_generation": "WHENEVER"})
    assert r.status_code == 400


# ── required_parts on the scope template detail ─────────────────────────────

def test_scope_detail_items_carry_required_parts(db, client, pm_env):
    tid = pm_env["scope"].id
    status, body = _get(client, f"/api/v1/pm-scope-templates/{tid}",
                        _token(client))
    assert status == 200
    assert body["items"][0]["required_parts"] == "Engine oil 5W-30 x 4L"


def test_scope_list_row_carries_the_linked_pm_template_label(
        db, client, pm_env):
    """The Jinja scope list has a "Linked PM Template" column; the API
    returned only pm_schedule_id, which is not renderable."""
    status, body = _get(client, "/api/v1/pm-scope-templates", _token(client))
    assert status == 200
    row = next(i for i in body["items"] if i["id"] == pm_env["scope"].id)
    assert row["pm_schedule_id"] == pm_env["sched"].id
    assert row["pm_schedule"] == "S02-00001"


# ── D5: export ──────────────────────────────────────────────────────────────

def test_export_returns_an_xlsx(db, client, pm_env):
    r = client.get("/api/v1/pm-templates/export",
                   headers={"Authorization": f"Bearer {_token(client)}"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["Content-Type"]
    # A real xlsx is a zip container.
    assert r.get_data()[:2] == b"PK"


def test_export_rejects_anonymous(db, client, pm_env):
    assert client.get("/api/v1/pm-templates/export").status_code == 401


def test_export_requires_the_same_permission_as_viewing(db, client, pm_env):
    """An export is a full dump. Guarding it more loosely than the
    screen would hand someone every row they cannot see on screen."""
    r = client.get("/api/v1/pm-templates/export",
                   headers={"Authorization": f"Bearer {_token(client, 'pmnobody')}"})
    assert r.status_code == 403


# ── Import ──────────────────────────────────────────────────────────────────

CSV = ("maintenance_type_code,vehicle_type_code,vehicle_make,vehicle_model,"
       "trigger_mode,interval_km,interval_days,priority\n"
       "PMS-PREV,LV-P,Toyota,Hilux,KM,10000,\n")


def _upload(client, token, text, field="csv_file"):
    return client.post(
        "/api/v1/pm-templates/import",
        headers={"Authorization": f"Bearer {token}"},
        data={field: (io.BytesIO(text.encode("utf-8")), "pm.csv")},
        content_type="multipart/form-data")


def test_import_creates_rows_and_reports_counts(db, client, pm_env):
    r = _upload(client, _token(client), CSV)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = json.loads(r.get_data(as_text=True))
    assert body["created"] == 1
    assert body["skipped"] == 0
    assert body["errors"] == []


def test_import_reports_row_level_errors_without_failing_the_request(
        db, client, pm_env):
    """The Jinja screen lists bad rows beside the good ones. A 400 for
    the whole file would leave the user with no idea which row was
    wrong."""
    bad = CSV + "NOPE-XX,LV-P,Toyota,Fortuner,KM,10000,\n"
    r = _upload(client, _token(client), bad)
    assert r.status_code == 200
    body = json.loads(r.get_data(as_text=True))
    assert body["created"] == 1
    assert len(body["errors"]) == 1
    assert "NOPE-XX" in body["errors"][0]


def test_import_is_idempotent_for_an_identical_row(db, client, pm_env):
    t = _token(client)
    _upload(client, t, CSV)
    body = json.loads(_upload(client, t, CSV).get_data(as_text=True))
    assert body["created"] == 0
    assert body["skipped"] == 1


def test_import_without_a_file_is_a_validation_error(db, client, pm_env):
    r = client.post("/api/v1/pm-templates/import",
                    headers={"Authorization": f"Bearer {_token(client)}"},
                    data={}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_import_rejects_a_user_with_no_pm_access(db, client, pm_env):
    r = _upload(client, _token(client, "pmnobody"), CSV)
    assert r.status_code == 403


def test_import_requires_create_not_merely_view(db, client, pm_env):
    """The load-bearing half. Import WRITES rows, so read access to the
    module must not be enough — otherwise anyone who can look at the PM
    Templates screen can bulk-insert into it."""
    view_only = _token(client, "pmviewer")
    # Same user, same token: can read the list...
    assert _get(client, "/api/v1/pm-templates", view_only)[0] == 200
    # ...and must not be able to import.
    assert _upload(client, view_only, CSV).status_code == 403


def test_export_is_allowed_for_a_view_only_user(db, client, pm_env):
    """The mirror image, and the reason the two guards differ: export
    reads, so `.view` is exactly right for it."""
    r = client.get("/api/v1/pm-templates/export",
                   headers={"Authorization":
                            f"Bearer {_token(client, 'pmviewer')}"})
    assert r.status_code == 200
