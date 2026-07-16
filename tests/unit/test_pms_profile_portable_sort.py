from app.modules.maintenance_config.service import (
    PMScheduleService, PMSProfileService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def test_get_profile_orders_correctly_with_mixed_null_sequence_positions(db):
    """Regression: PMSProfileService.get_profile() used SQLAlchemy's
    nullslast() for ordering, which has no native equivalent on MySQL
    (only PostgreSQL/Oracle support NULLS LAST directly) -- this passed
    fine against our SQLite test database but could raise a genuine SQL
    error against a real MySQL database, exactly the kind of dialect gap
    our test suite can't catch on its own. Verifies the fix (Python-level
    sort) produces the correct order regardless of backend."""
    mt = MaintenanceTypeService().create(code="NULLSORT-MT", name="Null Sort Test",
                                         category="PM")
    toyota = VehicleBrandService().create(name="Toyota NullSort")
    hilux = VehicleModelService().create(brand_id=toyota.id, name="Hilux NullSort")

    svc = PMScheduleService()
    # Deliberately created out of order, with one NULL sequence_position
    # in the middle, to prove the sort doesn't depend on insertion order.
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=20000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="NULLSORT-PROFILE", sequence_position=3)
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=1000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="NULLSORT-PROFILE", sequence_position=None)
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="NULLSORT-PROFILE", sequence_position=1)
    svc.create(maintenance_type_id=mt.id, trigger_mode="KM", interval_km=10000,
               vehicle_brand_id=toyota.id, vehicle_model_id=hilux.id,
               profile_code="NULLSORT-PROFILE", sequence_position=2)

    packages = PMSProfileService().get_profile("NULLSORT-PROFILE")
    # Non-NULL sequence_position rows first, in ascending order; the NULL
    # one (no explicit sequence) sorts last, matching "nulls last" intent.
    assert [p.sequence_position for p in packages] == [1, 2, 3, None]
    assert [p.interval_km for p in packages] == [5000, 10000, 20000, 1000]
