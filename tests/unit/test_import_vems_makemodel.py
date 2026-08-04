import openpyxl

from import_vems_makemodel import import_make_model


def _make_makemodel_workbook(path, rows):
    """A small workbook shaped exactly like the real VEMS 'Make and
    Model' export -- same sheet name, same column positions -- so these
    tests exercise the real import_make_model() code path without
    depending on a real fleet's uploaded file.

    All three original tests here hardcoded
    "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx" -- a file
    that only ever existed in one past working session's sandbox, making
    them unrunnable on any other machine including a fresh clone.

    Column order (0-indexed, matching scripts/import_vems_makemodel.py):
      2 = make name, 5 = model name. The others are present but unused
      by the import, so they're filled with placeholders for realism.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Make and Model"
    ws.append(["Make_Idx", "Make_CD", "Description", "Brand_Idx",
              "Model_CD", "Description", "Make_Pidx", "CommercialYield"])
    for make_name, model_name in rows:
        ws.append([1, "MK", make_name, 1, "MD", model_name, 1, "Y"])
    wb.save(path)
    return path


def test_dry_run_reports_counts_without_writing(tmp_path, db):
    path = _make_makemodel_workbook(tmp_path / "mm.xlsx", [
        ("Toyota", "Hilux"), ("Toyota", "Vios"), ("Ford", "Escape"),
        ("Isuzu", "Elf"),
    ])
    result = import_make_model(str(path), dry_run=True)
    assert result["total_rows"] == 4
    assert result["brands_created"] == 3   # Toyota, Ford, Isuzu

    from app.modules.master_data.vehicle_brand.models import VehicleBrand
    assert VehicleBrand.query.count() == 0   # nothing actually written


def test_real_import_creates_brands_and_models(tmp_path, db):
    path = _make_makemodel_workbook(tmp_path / "mm.xlsx", [
        ("Toyota", "Hilux"), ("Ford", "Escape"),
    ])
    result = import_make_model(str(path), dry_run=False)
    assert result["brands_created"] == 2

    from app.modules.master_data.vehicle_brand.models import (
        VehicleBrand, VehicleModel)
    ford = VehicleBrand.query.filter_by(name="Ford").first()
    assert ford is not None
    escape = VehicleModel.query.filter_by(brand_id=ford.id,
                                          name="Escape").first()
    assert escape is not None


def test_import_is_idempotent(tmp_path, db):
    path = _make_makemodel_workbook(tmp_path / "mm.xlsx", [
        ("Toyota", "Hilux"), ("Ford", "Escape"),
    ])
    r1 = import_make_model(str(path), dry_run=False)
    r2 = import_make_model(str(path), dry_run=False)
    assert r2["brands_created"] == 0
    assert r2["models_created"] == 0
    assert r2["brands_existing"] == r1["brands_created"] + r1["brands_existing"]


def test_blank_make_or_model_rows_are_skipped_not_counted_as_data(
        tmp_path, db):
    """A guard the real-file version could never exercise, since the
    real workbook has no deliberately blank rows to test against."""
    path = _make_makemodel_workbook(tmp_path / "mm.xlsx", [
        ("Toyota", "Hilux"), ("", "Orphan Model"), ("Orphan Brand", ""),
    ])
    result = import_make_model(str(path), dry_run=True)
    assert result["skipped_blank"] == 2
    assert result["brands_created"] == 1
