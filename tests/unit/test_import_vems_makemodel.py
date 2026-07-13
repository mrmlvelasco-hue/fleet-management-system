import sys
import os
sys.path.insert(0, "/home/claude/fms/scripts")

from import_vems_makemodel import import_make_model


def test_dry_run_reports_counts_without_writing(db):
    result = import_make_model(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx", dry_run=True)
    assert result["total_rows"] > 300
    assert result["brands_created"] > 0

    from app.modules.master_data.vehicle_brand.models import VehicleBrand
    assert VehicleBrand.query.count() == 0  # nothing actually written


def test_real_import_creates_brands_and_models(db):
    result = import_make_model(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx", dry_run=False)
    assert result["brands_created"] >= 30

    from app.modules.master_data.vehicle_brand.models import VehicleBrand, VehicleModel
    ford = VehicleBrand.query.filter_by(name="Ford").first()
    assert ford is not None
    escape = VehicleModel.query.filter_by(brand_id=ford.id, name="Escape").first()
    assert escape is not None


def test_import_is_idempotent(db):
    r1 = import_make_model(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx", dry_run=False)
    r2 = import_make_model(
        "/mnt/user-data/uploads/VEMS_Masterdata_for_vehicle.xlsx", dry_run=False)
    assert r2["brands_created"] == 0
    assert r2["models_created"] == 0
    assert r2["brands_existing"] == r1["brands_created"] + r1["brands_existing"]
