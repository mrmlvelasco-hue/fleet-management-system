import pytest

from app.core.search.searchable_service import SearchableService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


class VehicleSearchService(SearchableService):
    model = Vehicle
    search_fields = ["plate_number", "conduction_number", "brand", "model"]
    sortable_fields = ["brand", "model", "current_odometer"]

    def label(self, obj):
        ident = obj.plate_number or obj.conduction_number
        return f"{ident} — {obj.brand} {obj.model} ({obj.year})"

    def row(self, obj):
        return {"id": obj.id,
               "plate": obj.plate_number or obj.conduction_number,
               "brand": obj.brand, "model": obj.model,
               "branch": obj.branch.name if obj.branch else "",
               "status": obj.status}


@pytest.fixture()
def env(db):
    branch1 = BranchService().create(code="BR-SORT1", name="Branch A")
    branch2 = BranchService().create(code="BR-SORT2", name="Branch B")
    vt = VehicleTypeService().create(code="LV-SORT", name="Light", category="LIGHT")
    VehicleService().create(vehicle_type_id=vt.id, brand="Zeta", model="X",
                            year=2024, branch_id=branch1.id,
                            conduction_number="SORT-1", status="ACTIVE")
    VehicleService().create(vehicle_type_id=vt.id, brand="Alpha", model="Y",
                            year=2024, branch_id=branch2.id,
                            conduction_number="SORT-2", status="INACTIVE")
    return branch1, branch2, vt


def test_sort_ascending_by_brand(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q="", sort_by="brand", sort_dir="asc")
    assert [r.brand for r in results] == ["Alpha", "Zeta"]


def test_sort_descending_by_brand(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q="", sort_by="brand", sort_dir="desc")
    assert [r.brand for r in results] == ["Zeta", "Alpha"]


def test_invalid_sort_field_ignored(db, env):
    svc = VehicleSearchService()
    # "notarealfield" isn't in sortable_fields — should not raise, falls back to id order
    results, total = svc.search(q="", sort_by="notarealfield", sort_dir="asc")
    assert total == 2


def test_filter_by_branch(db, env):
    branch1, branch2, vt = env
    svc = VehicleSearchService()
    results, total = svc.search(q="", branch_id=branch1.id)
    assert total == 1
    assert results[0].brand == "Zeta"


def test_filter_by_status(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q="", status="INACTIVE")
    assert total == 1
    assert results[0].brand == "Alpha"


def test_to_table_response_shape(db, env):
    svc = VehicleSearchService()
    response = svc.to_table_response(q="", page=1, per_page=10,
                                      sort_by="brand", sort_dir="asc")
    assert "rows" in response and "total" in response and "page" in response
    assert response["rows"][0]["brand"] == "Alpha"
