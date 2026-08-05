"""Guards for the large-scale form UI merge (62 uploaded templates).

Two real problems were found and fixed during that merge:
  * vehicle_detail.html referenced vehicle.mv_file_no and
    vehicle.insured_value -- neither exists on the model (the real
    columns are mv_file_number and assured_value_current_year). Jinja's
    default Undefined renders a typo'd attribute as blank rather than
    raising, so this would NOT have crashed -- it would have silently
    shown "--" forever, even for vehicles that do have this data on
    file, which is worse than an error because nothing would ever flag
    it.
  * lookup_form.html had two leftover "[cite: 12]" citation artifacts
    visible in on-page help text.

Also guards against the class of bug those represent: every template in
the repo must at least be syntactically valid Jinja.
"""
import glob

import pytest


def test_every_template_parses(app):
    """A syntax error in any template is a real defect and should fail
    the suite, not wait to be discovered by a user hitting that page."""
    errors = []
    for path in glob.glob("app/**/templates/**/*.html", recursive=True):
        try:
            app.jinja_env.parse(open(path, encoding="utf-8").read())
        except Exception as exc:
            errors.append((path, str(exc)))
    assert not errors, f"Templates with Jinja syntax errors: {errors}"


def test_vehicle_detail_uses_the_real_mv_file_field(db):
    """Regression: the merged template referenced vehicle.mv_file_no,
    which doesn't exist -- the real column is mv_file_number."""
    html = open("app/modules/master_data/templates/master_data/"
               "vehicle_detail.html").read()
    assert "vehicle.mv_file_no " not in html
    assert "vehicle.mv_file_no}" not in html
    assert "vehicle.mv_file_number" in html


def test_vehicle_detail_uses_the_real_insured_value_field(db):
    """Regression: the merged template referenced vehicle.insured_value,
    which doesn't exist -- the real column is
    assured_value_current_year, matching the "This Year" label."""
    html = open("app/modules/master_data/templates/master_data/"
               "vehicle_detail.html").read()
    assert "vehicle.insured_value" not in html
    assert "vehicle.assured_value_current_year" in html


def test_no_citation_artifacts_in_any_template():
    """Leftover "[cite: N]" markers are a specific, recognisable defect
    class from AI-assisted drafts that weren't cleaned up before being
    treated as final copy -- catch it everywhere, not just where it was
    first found."""
    offenders = []
    for path in glob.glob("app/**/templates/**/*.html", recursive=True):
        text = open(path, encoding="utf-8").read()
        if "[cite:" in text or "[cite]" in text:
            offenders.append(path)
    assert not offenders, f"Citation artifacts left in: {offenders}"


def test_vehicle_detail_renders_real_field_values(app, db):
    """End-to-end: the fixed field names must actually surface the
    vehicle's real data, not just avoid crashing."""
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    from app.modules.master_data.reference.service import VehicleTypeService
    from app.modules.master_data.org.service import BranchService
    from app.modules.master_data.vehicle.service import VehicleService

    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")

    vt = VehicleTypeService().create(code="LV-UIM", name="Light",
                                     category="LIGHT")
    branch = BranchService().create(code="BR-UIM", name="Branch")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2022,
        branch_id=branch.id, conduction_number="UIM-1")
    vehicle.mv_file_number = "MVF-99887"
    vehicle.assured_value_current_year = 450000
    db.session.commit()

    client = app.test_client()
    client.post("/login", data={"username": "admin",
                                "password": "Testpass123!"})
    html = client.get(f"/master/vehicles/{vehicle.id}").get_data(as_text=True)
    assert "MVF-99887" in html
    assert "450,000.00" in html
