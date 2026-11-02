"""Dashboard loading performance -- the Company impact (2026-11-02).

Michael: "please check the performance of the dashboard for the impact
of the company we need to fix or improve the loading in the dashboard
all as faster as possible ... before i apply all the chnages in the
VSP server."

Root cause found by comparing query counts with and without a
company_id filter: RegistrationDueCalculationService.get_all_due_vehicles()
-- unlike its sibling PMDueCalculationService.get_all_due_vehicles(),
which already does `.options(joinedload(Vehicle.branch))` -- fetches
vehicles with NO eager load of `branch` at all. registration_status()'s
company_id narrowing (and GET /dashboard/bootstrap's own due_registration
block) then calls effective_company_id(vehicle) for every row in the due
list, which falls back to `vehicle.branch.company_id` for any vehicle
without its own company_id override -- lazy-loading Branch ONE ROW AT A
TIME. This is exactly the "scales with fleet size" N+1 shape this
project has already fixed twice elsewhere (Vehicle list, MO list), now
reintroduced by the Company filtering added on top of a query that was
never updated to eager-load what the new code path needs.
"""
import pytest
from sqlalchemy import event

from app.core.dashboard_analytics_service import DashboardAnalyticsService
from app.modules.master_data.org.service import (
    BranchService, OrgCompanyService, RegionService, AreaDistrictService)
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


def _count_queries(engine, fn):
    count = {"n": 0}

    def _listener(conn, cursor, statement, parameters, context, executemany):
        count["n"] += 1
    event.listen(engine, "before_cursor_execute", _listener)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", _listener)
    return count["n"]


def _company(suffix):
    region = RegionService().create(code=f"RG-DPF{suffix}",
                                    name=f"Region DPF {suffix}")
    area = AreaDistrictService().create(code=f"AD-DPF{suffix}",
                                        name=f"Area DPF {suffix}",
                                        region_id=region.id)
    return OrgCompanyService().create(short_name=f"Co DPF-{suffix}",
                                      region_id=region.id,
                                      area_district_id=area.id)


@pytest.fixture()
def fleet(db):
    company = _company("A")
    branch = BranchService().create(code="BR-DPF", name="Branch DPF",
                                    company_id=company.id)
    vt = VehicleTypeService().create(code="LV-DPF", name="Light",
                                     category="LIGHT")
    # No registration dates set -- every one of these lands in the due
    # list as NO_RECORD, which is exactly the scenario a fresh or
    # partially-imported fleet (like a demo, or week one on the VPS)
    # presents, and the worst case for this bug: every single row falls
    # back to the branch lookup since none carries its own company_id.
    vehicles = [
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
            year=2024, branch_id=branch.id, conduction_number=f"DPF-{i}")
        for i in range(12)
    ]
    db.session.commit()
    return company, branch, vehicles


def test_registration_status_company_filter_does_not_scale_with_fleet_size(
        db, fleet):
    from app.extensions import db as _db
    company, branch, vehicles = fleet

    def _run():
        DashboardAnalyticsService().registration_status(company_id=company.id)

    n = _count_queries(_db.engine, _run)
    # A small FIXED number regardless of fleet size -- confirmed at 29
    # for 12 vehicles before this fix (dominated by a 2-query-per-
    # vehicle brand/model lookup that ignored the templates prefetch
    # sitting right next to it); 7 after, and critically does not grow
    # with vehicle count the way 29 would have at 24 or 120 vehicles.
    assert n <= 10, f"expected a small fixed number of queries, got {n}"


def test_registration_due_calculation_query_count_does_not_grow_with_fleet_size(
        db, fleet):
    """The actual regression guard: double the fleet and the query count
    must stay flat, not double -- the shape an N+1 always has."""
    from app.extensions import db as _db
    from app.modules.registration_config.service import (
        RegistrationDueCalculationService)
    company, branch, vehicles = fleet

    def _run():
        RegistrationDueCalculationService().get_all_due_vehicles()

    n_small = _count_queries(_db.engine, _run)

    vt = VehicleTypeService().create(code="LV-DPF2", name="Light",
                                     category="LIGHT")
    for i in range(12, 36):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
            year=2024, branch_id=branch.id, conduction_number=f"DPF-{i}")
    db.session.commit()

    n_large = _count_queries(_db.engine, _run)
    assert n_large <= n_small + 2, (
        f"query count grew with fleet size ({n_small} -> {n_large}): "
        "an N+1 has crept back in")
