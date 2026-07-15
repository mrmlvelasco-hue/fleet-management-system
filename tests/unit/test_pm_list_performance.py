from sqlalchemy import event

from app.extensions import db
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)
from app.modules.master_data.reference.service import MaintenanceTypeService
from app.modules.master_data.vehicle_brand.service import (
    VehicleBrandService, VehicleModelService)


def _count_queries(fn):
    queries = []

    def _listener(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", _listener)
    try:
        result = fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", _listener)
    return result, len(queries)


def _make_schedules(db, count):
    mt = MaintenanceTypeService().create(code="PERFTEST-MT", name="Perf Test Type",
                                         category="PREVENTIVE")
    for i in range(count):
        # A distinct brand/model per schedule — mirrors the real VEMS
        # dataset's diversity (34 brands, 338 models) rather than letting
        # SQLAlchemy's identity map quietly cache one shared object and
        # mask the N+1 problem.
        brand = VehicleBrandService().create(name=f"PerfBrand{i}")
        model = VehicleModelService().create(brand_id=brand.id, name=f"PerfModel{i}")
        PMScheduleService().create(
            maintenance_type_id=mt.id, trigger_mode="KM", interval_km=5000 + i,
            vehicle_brand_id=brand.id, vehicle_model_id=model.id)


def test_schedule_list_does_not_n_plus_1_on_relationships(db):
    """Rendering the PM Template list touches vehicle_brand, vehicle_model,
    vehicle_type, and maintenance_type per row — without eager loading,
    listing N rows takes roughly 1 + 4*N queries. With eager loading it
    should stay flat regardless of N."""
    _make_schedules(db, 25)

    def _render():
        items = PMScheduleService().list()
        # Touch every relationship the list template actually accesses.
        for s in items:
            _ = s.vehicle_brand.name if s.vehicle_brand else None
            _ = s.vehicle_model_ref.name if s.vehicle_model_ref else None
            _ = s.vehicle_type.name if s.vehicle_type else None
            _ = s.maintenance_type.name
        return items

    items, query_count = _count_queries(_render)
    assert len(items) == 25
    # A small constant number of queries (base + a handful of eager-load
    # batches), not proportional to the number of rows.
    assert query_count < 10, f"Expected a bounded query count, got {query_count}"


def test_scope_template_list_does_not_n_plus_1_on_relationships(db):
    mt = MaintenanceTypeService().create(code="PERFTEST-SCOPE-MT", name="Perf Scope",
                                         category="PREVENTIVE")
    for i in range(20):
        PMScopeTemplateService().create(
            maintenance_type_id=mt.id, name=f"Perf Scope {i}",
            items=[{"activity_code": f"A{i}-{j}",
                   "activity_description": f"Activity {j}", "sort_order": j}
                  for j in range(5)])

    def _render():
        items = PMScopeTemplateService().list()
        for t in items:
            _ = t.maintenance_type.name
            _ = list(t.items)
            _ = t.pm_schedule
        return items

    items, query_count = _count_queries(_render)
    assert len(items) == 20
    assert query_count < 10, f"Expected a bounded query count, got {query_count}"
