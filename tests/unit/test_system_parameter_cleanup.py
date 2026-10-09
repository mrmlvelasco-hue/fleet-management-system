"""Retiring the System Parameters that turned out to be dead.

An audit of all seeded parameters against the code that actually reads
them found 9 that were seeded but consumed nowhere:
  - COMPANY_NAME: superseded by CompanyProfile / company_service.py;
    the {COMPANY_NAME} token in print templates is substituted from
    that, never from this SystemParameter row.
  - VEHICLE_LIST_COLUMNS, WO_NORMAL_LIST_COLUMNS, WO_PM_LIST_COLUMNS:
    no list view reads a configurable column count anywhere.
  - IMG_VEHICLE_FRONT_BACK_PX, IMG_CR_PX, IMG_PERSON_ATD_PX,
    IMG_LTO_ENGINE_PX, IMG_ENGINE_CR_PX: one hardcoded limit per VEMS-era
    picture category, but document types are admin-configurable via
    Lookup Maintenance, not a fixed category list -- there was never a
    matching upload path for these to plug into.

The 5 image parameters are replaced by one generic
ATTACHMENT_IMAGE_MAX_DIMENSION_PX, applied to every image regardless of
category (see image_resize.py / attachment_service.py). The other 4
have no replacement -- they are simply gone.
"""
from app.cli import (_seed_system_parameters,
                     _retire_dead_system_parameters)
from app.modules.system_admin.models import SystemParameter

DEAD_CODES = [
    "COMPANY_NAME",
    "VEHICLE_LIST_COLUMNS",
    "WO_NORMAL_LIST_COLUMNS",
    "WO_PM_LIST_COLUMNS",
    "IMG_VEHICLE_FRONT_BACK_PX",
    "IMG_CR_PX",
    "IMG_PERSON_ATD_PX",
    "IMG_LTO_ENGINE_PX",
    "IMG_ENGINE_CR_PX",
]


def test_dead_parameters_are_no_longer_seeded(db):
    _seed_system_parameters()
    db.session.commit()
    codes = {row.code for row in SystemParameter.query.all()}
    for code in DEAD_CODES:
        assert code not in codes, f"{code} should no longer be seeded"


def test_the_new_generic_image_dimension_parameter_is_seeded(db):
    _seed_system_parameters()
    db.session.commit()
    row = SystemParameter.query.filter_by(
        code="ATTACHMENT_IMAGE_MAX_DIMENSION_PX").first()
    assert row is not None
    assert row.data_type == "INTEGER"


def test_the_previously_invisible_attachment_parameters_are_now_seeded(db):
    """ATTACHMENT_ALLOWED_EXTENSIONS and ATTACHMENT_MAX_SIZE_MB were read
    by AttachmentService from the day it shipped but never seeded -- the
    same invisible-parameter pattern as AUTO_PR_FROM_MO/PMS. Seeding them
    is what makes them visible and editable from System Administration
    instead of silently defaulted in code."""
    _seed_system_parameters()
    db.session.commit()
    codes = {row.code for row in SystemParameter.query.all()}
    assert "ATTACHMENT_ALLOWED_EXTENSIONS" in codes
    assert "ATTACHMENT_MAX_SIZE_MB" in codes


def test_retire_removes_existing_dead_rows_on_an_upgraded_database(db):
    """A database that already has the dead rows (seeded by an older
    version of this code before this cleanup existed) must have them
    actually removed, not just skipped going forward."""
    for code in DEAD_CODES:
        db.session.add(SystemParameter(
            code=code, value="1", data_type="STRING", group_name="LEGACY"))
    db.session.commit()

    _retire_dead_system_parameters()
    db.session.commit()

    codes = {row.code for row in SystemParameter.query.all()}
    for code in DEAD_CODES:
        assert code not in codes


def test_retire_is_idempotent(db):
    _retire_dead_system_parameters()
    db.session.commit()
    _retire_dead_system_parameters()
    db.session.commit()
    codes = {row.code for row in SystemParameter.query.all()}
    for code in DEAD_CODES:
        assert code not in codes


def test_retire_does_not_touch_an_admin_edited_value_of_a_live_parameter(db):
    """Sanity check that retirement is scoped to exactly the dead code
    list, not a broad cleanup that could catch a live parameter."""
    db.session.add(SystemParameter(
        code="SESSION_TIMEOUT_MINUTES", value="45",
        data_type="INTEGER", group_name="SECURITY"))
    db.session.commit()

    _retire_dead_system_parameters()
    db.session.commit()

    row = SystemParameter.query.filter_by(
        code="SESSION_TIMEOUT_MINUTES").first()
    assert row is not None
    assert row.value == "45"
