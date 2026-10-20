"""RegistrationTemplateService.find_applicable() and the registration
checklist it feeds.

Reported from UAT 2026-10-20: a driver completed an LTO registration
(OR/CR entered) whose embedded checklist was never ticked -- it had no
items to tick. Matches the already-catalogued KI-19: "A registration
template matched by Brand never applies (and 'Interval (Years)' is
ignored)."

Two independent bugs, confirmed by reading the code:

1. `find_applicable()` compared `t.vehicle_brand_id`/`t.vehicle_model_id`
   against `vehicle.vehicle_brand_id`/`vehicle.vehicle_model_id` --
   columns that do not exist on Vehicle at all (Vehicle stores plain
   free-text `brand`/`model` strings; see vehicle_brand/models.py's
   module docstring). `getattr(vehicle, "vehicle_brand_id", None)` was
   therefore always `None`, so a Brand(+Model) template could never
   match, for ANY vehicle -- not just a Brand-only one. PM Template
   matching solved exactly this already
   (PMDueCalculationService._resolve_vehicle_brand_model_ids); the fix
   here mirrors that same resolution.

2. Even once resolved, a Brand-only template (brand set, model left
   blank -- a legitimate, lesser-specific tier the admin screen allows)
   was still unreachable: the brand+model tier required BOTH fields on
   the template, and there was no separate Brand-only tier to catch it
   before falling through to Vehicle Type / Global.

3. `VehicleRegistrationService.create()`'s expiry_date calculation
   never consulted the matched template's `interval_years` at all --
   only the `validity_years` argument / DEFAULT_VALIDITY_YEARS, so a
   template's "Interval (Years)" setting had no effect.
"""
from datetime import date

import pytest

from app.extensions import db
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)
from app.modules.registration_config.service import RegistrationTemplateService
from app.modules.transactions.vehicle_registration.service import (
    VehicleRegistrationService)
from app.modules.user_management.models import User
from app.core.security.password import hash_password


@pytest.fixture()
def rig(db):
    branch = BranchService().create(code="BR-RT", name="RT Branch")
    vt = VehicleTypeService().create(code="LV-RT", name="Light", category="LIGHT")
    brand = VehicleBrandService().create("Toyota")
    model = VehicleModelService().create(brand.id, "Vios")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Vios", year=2020,
        branch_id=branch.id, conduction_number="RT-001",
        plate_number="RTZ-1111")
    user = User(username="rtuser", email="rt@example.com",
                password_hash=hash_password("secret123"), is_active=True)
    db.session.add(user)
    db.session.commit()
    return branch, vt, brand, model, vehicle, user


def test_brand_only_template_matches_a_vehicle_of_that_brand(rig):
    """A Brand-only template (model left blank) is a legitimate,
    lesser-specific tier -- it must still apply, not silently vanish."""
    branch, vt, brand, model, vehicle, user = rig
    tmpl = RegistrationTemplateService().create(vehicle_brand_id=brand.id)

    found = RegistrationTemplateService().find_applicable(vehicle)

    assert found is not None
    assert found.id == tmpl.id


def test_brand_and_model_template_still_wins_over_brand_only(rig):
    branch, vt, brand, model, vehicle, user = rig
    brand_only = RegistrationTemplateService().create(vehicle_brand_id=brand.id)
    brand_model = RegistrationTemplateService().create(
        vehicle_brand_id=brand.id, vehicle_model_id=model.id)

    found = RegistrationTemplateService().find_applicable(vehicle)

    assert found.id == brand_model.id


def test_brand_only_template_beats_a_vehicle_type_template(rig):
    branch, vt, brand, model, vehicle, user = rig
    RegistrationTemplateService().create(vehicle_type_id=vt.id)
    brand_only = RegistrationTemplateService().create(vehicle_brand_id=brand.id)

    found = RegistrationTemplateService().find_applicable(vehicle)

    assert found.id == brand_only.id


def test_a_different_brand_does_not_match(rig):
    branch, vt, brand, model, vehicle, user = rig
    other_brand = VehicleBrandService().create("Isuzu")
    RegistrationTemplateService().create(vehicle_brand_id=other_brand.id)

    found = RegistrationTemplateService().find_applicable(vehicle)

    assert found is None


def test_registration_created_under_a_brand_only_template_gets_its_checklist(
        rig):
    """The exact symptom reported: the registration's embedded checklist
    was empty because no template ever matched, so there was nothing to
    tick."""
    branch, vt, brand, model, vehicle, user = rig
    RegistrationTemplateService().create(
        vehicle_brand_id=brand.id,
        items=[{"activity_code": "A1", "activity_description": "Check OR/CR"}])

    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date(2026, 1, 1), user=user)

    assert len(reg.checklist_items) == 1
    assert reg.checklist_items[0].activity_code == "A1"


def test_create_uses_the_matched_templates_interval_years(rig):
    """'Interval (Years)' on the template must actually drive the
    computed expiry date when the caller does not override it with an
    explicit validity_years."""
    branch, vt, brand, model, vehicle, user = rig
    RegistrationTemplateService().create(
        vehicle_brand_id=brand.id, interval_years=5)

    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date(2026, 1, 1), user=user)

    assert reg.expiry_date == date(2031, 1, 1)


def test_explicit_validity_years_still_overrides_the_template(rig):
    branch, vt, brand, model, vehicle, user = rig
    RegistrationTemplateService().create(
        vehicle_brand_id=brand.id, interval_years=5)

    reg = VehicleRegistrationService().create(
        vehicle_id=vehicle.id, registration_type="NEW",
        registration_date=date(2026, 1, 1), user=user, validity_years=1)

    assert reg.expiry_date == date(2027, 1, 1)
