import pytest

from app.core.search.searchable_service import SearchableService
from app.modules.master_data.vehicle.models import Vehicle
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.org.service import BranchService


class VehicleSearchService(SearchableService):
    model = Vehicle
    search_fields = ["plate_number", "conduction_number", "brand", "model"]

    def label(self, obj):
        ident = obj.plate_number or obj.conduction_number
        return f"{ident} — {obj.brand} {obj.model} ({obj.year})"


@pytest.fixture()
def env(db):
    branch = BranchService().create(code="BR-SEARCH", name="Search Branch")
    vt = VehicleTypeService().create(code="LV-SEARCH", name="Light",
                                     category="LIGHT")
    for i in range(25):
        VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota" if i % 2 == 0 else "Honda",
            model="Hilux" if i % 2 == 0 else "City", year=2024,
            branch_id=branch.id, conduction_number=f"SRCH-{i:03d}")
    return branch, vt


def test_search_by_plate_or_conduction_number(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q="SRCH-001")
    assert total == 1
    assert results[0].conduction_number == "SRCH-001"


def test_search_by_brand(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q="Toyota")
    assert total == 13  # every other one, 0..24 even indices = 13


def test_search_paginates(db, env):
    svc = VehicleSearchService()
    page1, total = svc.search(q="", page=1, per_page=10)
    assert len(page1) == 10
    assert total == 25
    page3, total = svc.search(q="", page=3, per_page=10)
    assert len(page3) == 5


def test_to_select2_response_shape(db, env):
    svc = VehicleSearchService()
    response = svc.to_select2_response(q="Toyota", page=1, per_page=10)
    assert "results" in response and "pagination" in response
    assert len(response["results"]) == 10
    assert response["pagination"]["more"] is True
    assert set(response["results"][0].keys()) == {"id", "text"}


def test_empty_query_returns_all(db, env):
    svc = VehicleSearchService()
    results, total = svc.search(q=None, per_page=100)
    assert total == 25
