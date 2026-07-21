"""One-time data migration: import VEMS_Masterdata_for_vehicle.xlsx's
'Make and Model' sheet into our VehicleBrand/VehicleModel master tables.
Idempotent — safe to re-run; existing Brand/Model names are skipped.
"""
import os
import sys

# See import_pm_task_list.py for why this is needed -- running this
# script standalone otherwise fails to import `app` at all, or worse,
# silently resolves it to an unrelated pip-installed package of the
# same name.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# See import_pm_task_list.py for the full explanation: without this, a
# raw script invocation silently falls back to an unmigrated local
# SQLite file instead of the real configured (e.g. MySQL) database.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import openpyxl

from app.extensions import db
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def import_make_model(xlsx_path: str, dry_run: bool = False) -> dict:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["Make and Model"]
    rows = list(ws.iter_rows(min_row=2, values_only=True))

    brand_svc = VehicleBrandService()
    model_svc = VehicleModelService()

    brands_created, brands_existing = 0, 0
    models_created, models_existing = 0, 0
    skipped_blank = 0
    brand_cache = {}  # name.lower() -> VehicleBrand instance

    for row in rows:
        # (Make_Idx, Make_CD, Description(make name), Brand_Idx, Model_CD,
        #  Description(model name), Make_Pidx, CommercialYield)
        make_name = str(row[2] or "").strip()
        model_name = str(row[5] or "").strip()
        if not make_name or not model_name:
            skipped_blank += 1
            continue

        key = make_name.lower()
        if key not in brand_cache:
            brand = brand_svc.get_by_name(make_name)
            if brand:
                brands_existing += 1
            else:
                brand = None if dry_run else brand_svc.create(name=make_name)
                brands_created += 1
            brand_cache[key] = brand
        brand = brand_cache[key]

        if brand is None:  # dry-run and brand doesn't exist yet
            models_created += 1
            continue

        if model_svc.get_by_name_and_brand(model_name, brand.id):
            models_existing += 1
        else:
            if not dry_run:
                model_svc.create(brand_id=brand.id, name=model_name)
            models_created += 1

    return {
        "total_rows": len(rows),
        "brands_created": brands_created,
        "brands_existing": brands_existing,
        "models_created": models_created,
        "models_existing": models_existing,
        "skipped_blank": skipped_blank,
    }


if __name__ == "__main__":
    import sys
    from app import create_app
    path = sys.argv[1] if len(sys.argv) > 1 else "VEMS_Masterdata_for_vehicle.xlsx"
    dry = "--dry-run" in sys.argv
    app = create_app()
    with app.app_context():
        result = import_make_model(path, dry_run=dry)
        print(result)
