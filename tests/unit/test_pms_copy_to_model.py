"""Copy one model's PMS to another model (2026-11-17).

Requested: "we need to copy PMS of one vehicle to new model ... all PMS
schedule of the Strada to Triton because this is same PMS".

The file-based way of doing that (export, change the Model in Excel,
upload) went wrong in three places in the file the client actually
built -- see test_pm_task_list_import_safeguards.py. This is the same
result as one server-side action, with nothing to retype: every active
PM package of the source model, with its checklist, is copied to the
target model under new PMS Profile codes.
"""
import json

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db as _db
from app.modules.maintenance_config.copy_service import (
    PMSCopyError, PMSCopyService)
from app.modules.maintenance_config.models import (
    PMSchedule, PMScopeItem, PMScopeTemplate)
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.user_management.models import Permission, Role, User


@pytest.fixture()
def env(db):
    brand = VehicleBrandService().create("Mitsubishi")
    strada = VehicleModelService().create(brand.id, "Strada")
    triton = VehicleModelService().create(brand.id, "Triton")
    toyota = VehicleBrandService().create("Toyota")
    hilux = VehicleModelService().create(toyota.id, "Hilux")
    prev = MaintenanceTypeService().create(
        code="PMS-PREV", name="Preventive Maintenance Service", category="PM")
    tire = MaintenanceTypeService().create(
        code="PMS-TIRE", name="Tire Replacement", category="PM")

    def package(mtype, code, seq, km_text, cum_km, interval_km, steps,
                **extra):
        description = (f"{mtype.name} Mitsubishi Strada {km_text}")
        sched = PMScheduleService().create(
            maintenance_type_id=mtype.id, trigger_mode="HYBRID",
            vehicle_brand_id=brand.id, vehicle_model_id=strada.id,
            profile_code=code, profile_description=description,
            sequence_position=seq, interval_km=interval_km,
            interval_days=90, cumulative_km=cum_km,
            work_description_template=f"{km_text} servicing of pm2 pm3",
            priority="MEDIUM", **extra)
        if steps:
            PMScopeTemplateService().create(
                maintenance_type_id=mtype.id,
                name=f"{description} - Package {seq}",
                description=description, pm_schedule_id=sched.id,
                items=[{"activity_code": f"{code}-{i + 1:03d}",
                        "activity_description": text, "sort_order": i}
                       for i, text in enumerate(steps)])
        return sched

    package(prev, "S02-00032", 1, "1,000 km", 1000, 1000,
            ["Change engine oil.", "Check tire pressure."],
            notify_before_km=500, notify_before_days=7)
    package(prev, "S02-00032", 2, "5,000 km", 5000, 5000,
            ["Rotate tires.", "Inspect brake pads.", "Top up coolant."])
    package(tire, "S03-00031", 1, "45,000 km", 45000, 45000,
            ["Replace all four tires."])
    return {"brand": brand, "strada": strada, "triton": triton,
            "hilux": hilux, "prev": prev, "tire": tire}


def _copy(env, target="triton", dry_run=False):
    return PMSCopyService().copy_to_model(
        source_model_id=env["strada"].id,
        target_model_id=env[target].id, dry_run=dry_run)


def _of_model(model):
    return (PMSchedule.query.filter_by(vehicle_model_id=model.id)
            .order_by(PMSchedule.profile_code,
                      PMSchedule.sequence_position).all())


# ── What gets copied ────────────────────────────────────────────────────────

def test_every_package_of_the_source_model_is_copied(env):
    result = _copy(env)
    rows = _of_model(env["triton"])
    assert len(rows) == 3
    assert result["packages_created"] == 3
    assert all(r.vehicle_brand_id == env["brand"].id for r in rows)
    assert all(r.vehicle_make is None and r.vehicle_model is None
               for r in rows)


def test_intervals_and_settings_are_copied_exactly(env):
    _copy(env)
    source = {(r.maintenance_type_id, r.sequence_position): r
              for r in _of_model(env["strada"])}
    for copy in _of_model(env["triton"]):
        original = source[(copy.maintenance_type_id, copy.sequence_position)]
        for field in ("trigger_mode", "interval_km", "interval_days",
                      "interval_hours", "cumulative_km", "priority",
                      "notify_before_km", "notify_before_days",
                      "escalate_if_overdue", "next_pms_generation",
                      "next_due_calculation_method", "vehicle_type_id"):
            assert getattr(copy, field) == getattr(original, field), field


def test_every_checklist_is_copied_line_for_line(env):
    result = _copy(env)
    copies = _of_model(env["triton"])
    by_seq = {(r.maintenance_type_id, r.sequence_position): r for r in copies}
    first = by_seq[(env["prev"].id, 1)]
    second = by_seq[(env["prev"].id, 2)]
    assert [i.activity_description for i in first.scope_templates[0].items] == [
        "Change engine oil.", "Check tire pressure."]
    assert [i.activity_description for i in second.scope_templates[0].items] == [
        "Rotate tires.", "Inspect brake pads.", "Top up coolant."]
    assert result["scope_items_created"] == 6
    assert result["scope_templates_created"] == 3


def test_the_source_model_is_left_exactly_as_it_was(env):
    before = [(r.id, r.profile_code, r.profile_description,
               len(r.scope_templates[0].items))
              for r in _of_model(env["strada"])]
    _copy(env)
    after = [(r.id, r.profile_code, r.profile_description,
              len(r.scope_templates[0].items))
             for r in _of_model(env["strada"])]
    assert before == after
    assert PMScopeItem.query.count() == 12


# ── New profile codes ───────────────────────────────────────────────────────

def test_the_copy_gets_its_own_profile_codes_not_the_sources(env):
    result = _copy(env)
    codes = {r.profile_code for r in _of_model(env["triton"])}
    assert codes == {"MITSUBISHI-TRITON-PMS-PREV-1",
                     "MITSUBISHI-TRITON-PMS-TIRE-1"}
    assert {(p["source_profile_code"], p["new_profile_code"])
            for p in result["profiles"]} == {
        ("S02-00032", "MITSUBISHI-TRITON-PMS-PREV-1"),
        ("S03-00031", "MITSUBISHI-TRITON-PMS-TIRE-1")}
    # The Strada's own profiles still hold only the Strada's packages.
    assert PMSchedule.query.filter_by(profile_code="S02-00032").count() == 2


def test_a_code_already_in_use_is_never_reused(env):
    # Someone already used the code the copy would pick first.
    PMScheduleService().create(
        maintenance_type_id=env["prev"].id, trigger_mode="KM",
        vehicle_brand_id=env["hilux"].brand_id,
        vehicle_model_id=env["hilux"].id,
        profile_code="MITSUBISHI-TRITON-PMS-PREV-1", interval_km=5000)
    _copy(env)
    codes = {r.profile_code for r in _of_model(env["triton"])}
    assert "MITSUBISHI-TRITON-PMS-PREV-2" in codes
    assert "MITSUBISHI-TRITON-PMS-PREV-1" not in codes


def test_checklist_activity_codes_follow_the_new_profile_code(env):
    _copy(env)
    copies = _of_model(env["triton"])
    codes = [i.activity_code for r in copies
             for i in r.scope_templates[0].items]
    assert "MITSUBISHI-TRITON-PMS-PREV-1-001" in codes
    assert not any(c.startswith("S02-00032") for c in codes)
    assert all(len(c) <= 40 for c in codes)


def test_long_brand_and_model_names_still_fit_the_code_columns(env):
    brand = VehicleBrandService().create("Mercedes-Benz Commercial Vehicles")
    model = VehicleModelService().create(brand.id, "Sprinter 516 CDI Panel Van")
    PMSCopyService().copy_to_model(source_model_id=env["strada"].id,
                                   target_model_id=model.id, dry_run=False)
    rows = PMSchedule.query.filter_by(vehicle_model_id=model.id).all()
    assert rows
    assert all(len(r.profile_code) <= 36 for r in rows)
    assert all(len(i.activity_code) <= 40
               for r in rows for i in r.scope_templates[0].items)
    assert len({r.profile_code for r in rows}) == 2


# ── Names follow the target model ───────────────────────────────────────────

def test_descriptions_name_the_target_model(env):
    _copy(env)
    for r in _of_model(env["triton"]):
        assert "Triton" in r.profile_description
        assert "Strada" not in r.profile_description
        template = r.scope_templates[0]
        assert "Strada" not in template.name
        assert "Triton" in template.name
        assert "Strada" not in (template.description or "")


def test_a_copy_to_another_brand_renames_brand_and_model_together(env):
    _copy(env, target="hilux")
    rows = _of_model(env["hilux"])
    assert rows[0].profile_description.endswith("Toyota Hilux 1,000 km")
    assert all("Mitsubishi" not in r.profile_description for r in rows)
    assert {r.profile_code for r in rows} == {
        "TOYOTA-HILUX-PMS-PREV-1", "TOYOTA-HILUX-PMS-TIRE-1"}


def test_checklist_steps_and_work_descriptions_are_not_reworded(env):
    _copy(env)
    first = [r for r in _of_model(env["triton"])
             if r.cumulative_km == 1000][0]
    assert first.work_description_template == "1,000 km servicing of pm2 pm3"


# ── Dry run ─────────────────────────────────────────────────────────────────

def test_a_dry_run_reports_the_same_counts_and_saves_nothing(env):
    before = (PMSchedule.query.count(), PMScopeTemplate.query.count(),
              PMScopeItem.query.count())
    result = _copy(env, dry_run=True)
    assert result["dry_run"] is True
    assert result["packages_created"] == 3
    assert result["scope_items_created"] == 6
    assert {p["new_profile_code"] for p in result["profiles"]} == {
        "MITSUBISHI-TRITON-PMS-PREV-1", "MITSUBISHI-TRITON-PMS-TIRE-1"}
    assert (PMSchedule.query.count(), PMScopeTemplate.query.count(),
            PMScopeItem.query.count()) == before


def test_the_result_describes_each_profile(env):
    result = _copy(env, dry_run=True)
    prev = [p for p in result["profiles"]
            if p["source_profile_code"] == "S02-00032"][0]
    assert prev["maintenance_type"] == "Preventive Maintenance Service"
    assert prev["packages"] == 2
    assert prev["checklist_items"] == 5
    assert result["source"] == {"model_id": env["strada"].id,
                                "brand": "Mitsubishi", "model": "Strada"}
    assert result["target"] == {"model_id": env["triton"].id,
                                "brand": "Mitsubishi", "model": "Triton"}


# ── Refusals ────────────────────────────────────────────────────────────────

def test_a_target_that_already_has_pms_is_refused(env):
    _copy(env)
    with pytest.raises(PMSCopyError) as exc:
        _copy(env)
    assert "already has" in str(exc.value)
    assert len(_of_model(env["triton"])) == 3


def test_a_target_whose_pms_is_stored_as_text_is_also_refused(env):
    PMScheduleService().create(
        maintenance_type_id=env["prev"].id, trigger_mode="KM",
        vehicle_make="mitsubishi", vehicle_model="TRITON",
        profile_code="OLD-1", interval_km=5000)
    with pytest.raises(PMSCopyError):
        _copy(env)


def test_a_target_whose_pms_was_deactivated_can_be_copied_to(env):
    _copy(env)
    for r in _of_model(env["triton"]):
        r.is_active = False
    _db.session.commit()
    result = _copy(env)
    assert result["packages_created"] == 3
    active = [r for r in _of_model(env["triton"]) if r.is_active]
    assert {r.profile_code for r in active} == {
        "MITSUBISHI-TRITON-PMS-PREV-2", "MITSUBISHI-TRITON-PMS-TIRE-2"}


def test_a_source_with_no_pms_is_refused(env):
    with pytest.raises(PMSCopyError) as exc:
        PMSCopyService().copy_to_model(
            source_model_id=env["hilux"].id,
            target_model_id=env["triton"].id, dry_run=True)
    assert "no active PMS" in str(exc.value)


def test_copying_a_model_onto_itself_is_refused(env):
    with pytest.raises(PMSCopyError):
        PMSCopyService().copy_to_model(
            source_model_id=env["strada"].id,
            target_model_id=env["strada"].id, dry_run=True)


def test_an_unknown_model_is_refused(env):
    with pytest.raises(PMSCopyError):
        PMSCopyService().copy_to_model(
            source_model_id=env["strada"].id, target_model_id=999999,
            dry_run=True)


def test_inactive_source_packages_and_checklists_are_not_copied(env):
    strada = _of_model(env["strada"])
    retired = [r for r in strada if r.cumulative_km == 5000][0]
    retired.is_active = False
    _db.session.commit()
    result = _copy(env)
    assert result["packages_created"] == 2
    assert 5000 not in [r.cumulative_km for r in _of_model(env["triton"])]


def test_a_source_stored_as_text_is_copied_too(env):
    """A model that was not in Vehicle Models when its PMS was imported
    is stored by make/model text. Once the model exists it can still be
    used as a source."""
    isuzu = VehicleBrandService().create("Isuzu")
    dmax = VehicleModelService().create(isuzu.id, "D-Max")
    PMScheduleService().create(
        maintenance_type_id=env["prev"].id, trigger_mode="KM",
        vehicle_make="ISUZU", vehicle_model="d-max",
        profile_code="S02-00500", profile_description="PM Isuzu D-Max 5,000 km",
        sequence_position=1, interval_km=5000, cumulative_km=5000)
    result = PMSCopyService().copy_to_model(
        source_model_id=dmax.id, target_model_id=env["triton"].id,
        dry_run=False)
    assert result["packages_created"] == 1
    assert _of_model(env["triton"])[0].profile_description == (
        "PM Mitsubishi Triton 5,000 km")


def test_the_copied_pms_is_what_a_triton_vehicle_is_matched_to(env):
    _copy(env)
    matched = PMScheduleService().list_applicable_for_criteria(
        brand_name="Mitsubishi", model_name="Triton")
    assert len(matched) == 3
    assert {m.vehicle_model_id for m in matched} == {env["triton"].id}


# ── HTTP ────────────────────────────────────────────────────────────────────

@pytest.fixture()
def users(env):
    sync_permissions()
    _db.session.commit()
    role = Role(name="PM Copier")
    role.permissions = Permission.query.filter(
        Permission.code.in_(["pmschedule.create"])).all()
    _db.session.add(role)
    _db.session.flush()
    allowed = User(username="pmcopier", email="pmcopier@e.com",
                   password_hash=hash_password("secret123"), is_active=True)
    allowed.roles.append(role)
    outsider = User(username="pmnobody", email="pmnobody@e.com",
                    password_hash=hash_password("secret123"), is_active=True)
    _db.session.add_all([allowed, outsider])
    _db.session.commit()
    return env


def _token(client, username):
    r = client.post("/api/v1/auth/token",
                    json={"username": username, "password": "secret123"})
    return json.loads(r.get_data(as_text=True))["access_token"]


def _post(client, username, payload):
    r = client.post("/api/v1/pms-copy", json=payload,
                    headers={"Authorization":
                             f"Bearer {_token(client, username)}"})
    return r.status_code, json.loads(r.get_data(as_text=True))


def test_api_defaults_to_a_dry_run(client, users):
    status, body = _post(client, "pmcopier", {
        "source_model_id": users["strada"].id,
        "target_model_id": users["triton"].id})
    assert status == 200
    assert body["dry_run"] is True
    assert body["packages_created"] == 3
    assert _of_model(users["triton"]) == []


def test_api_copies_for_real_only_when_told_to(client, users):
    status, body = _post(client, "pmcopier", {
        "source_model_id": users["strada"].id,
        "target_model_id": users["triton"].id, "dry_run": False})
    assert status == 200
    assert body["dry_run"] is False
    assert len(_of_model(users["triton"])) == 3


def test_api_refusal_is_a_readable_400_not_a_500(client, users):
    _post(client, "pmcopier", {
        "source_model_id": users["strada"].id,
        "target_model_id": users["triton"].id, "dry_run": False})
    status, body = _post(client, "pmcopier", {
        "source_model_id": users["strada"].id,
        "target_model_id": users["triton"].id, "dry_run": False})
    assert status == 400
    assert body["error"] == "validation"
    assert "already has" in body["message"]


def test_api_requires_both_models(client, users):
    status, body = _post(client, "pmcopier", {
        "source_model_id": users["strada"].id})
    assert status == 400
    assert body["error"] == "validation"


def test_api_requires_pmschedule_create(client, users):
    status, _body = _post(client, "pmnobody", {
        "source_model_id": users["strada"].id,
        "target_model_id": users["triton"].id})
    assert status == 403
