"""The Vehicle Brand list's model-count link.

Reported: clicking the "N model(s)" link on a brand's row went to the
generic, unfiltered Vehicle Models list rather than the models
belonging specifically to that brand -- confirmed the link carried no
brand parameter at all before this fix.
"""
import pytest


@pytest.fixture()
def two_brands_with_models(app, db):
    from app.modules.master_data.vehicle_brand.service import (
        VehicleBrandService, VehicleModelService)
    toyota = VehicleBrandService().create(name="Toyota Test")
    honda = VehicleBrandService().create(name="Honda Test")
    VehicleModelService().create(brand_id=toyota.id, name="Hilux Test")
    VehicleModelService().create(brand_id=toyota.id, name="Vios Test")
    VehicleModelService().create(brand_id=honda.id, name="Civic Test")
    return toyota, honda


def _client(app, db):
    from app.core.security.registry import sync_permissions
    from app.cli import _seed_admin
    sync_permissions(); db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin", "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_brand_list_link_includes_the_brand_id(
        app, db, two_brands_with_models):
    toyota, _honda = two_brands_with_models
    html = _client(app, db).get(
        "/master/vehicle-brands").get_data(as_text=True)
    assert f"/master/vehicle-models?brand_id={toyota.id}" in html


def test_filtered_page_shows_only_that_brands_models(
        app, db, two_brands_with_models):
    toyota, honda = two_brands_with_models
    html = _client(app, db).get(
        f"/master/vehicle-models?brand_id={toyota.id}").get_data(as_text=True)
    assert "Hilux Test" in html
    assert "Vios Test" in html
    assert "Civic Test" not in html


def test_filtered_page_shows_a_clear_indicator_and_a_way_to_clear_it(
        app, db, two_brands_with_models):
    toyota, _honda = two_brands_with_models
    html = _client(app, db).get(
        f"/master/vehicle-models?brand_id={toyota.id}").get_data(as_text=True)
    assert "Showing models for" in html
    assert "Toyota Test" in html
    assert "Clear filter" in html
    assert '/master/vehicle-models"' in html  # the clear-filter link target


def test_unfiltered_page_still_shows_everything(
        app, db, two_brands_with_models):
    toyota, honda = two_brands_with_models
    html = _client(app, db).get(
        "/master/vehicle-models").get_data(as_text=True)
    assert "Hilux Test" in html
    assert "Civic Test" in html
    assert "Showing models for" not in html


def test_an_invalid_brand_id_does_not_crash_the_page(app, db):
    """A stale or tampered brand_id must degrade gracefully -- empty
    results with an honest indicator, not a 500."""
    r = _client(app, db).get("/master/vehicle-models?brand_id=999999")
    assert r.status_code == 200


def test_new_model_link_from_filtered_page_carries_the_brand_forward(
        app, db, two_brands_with_models):
    toyota, _honda = two_brands_with_models
    html = _client(app, db).get(
        f"/master/vehicle-models?brand_id={toyota.id}").get_data(as_text=True)
    assert f"/master/vehicle-models/new?brand_id={toyota.id}" in html


def test_new_model_form_preselects_the_brand_from_query_param(
        app, db, two_brands_with_models):
    toyota, _honda = two_brands_with_models
    html = _client(app, db).get(
        f"/master/vehicle-models/new?brand_id={toyota.id}"
    ).get_data(as_text=True)
    assert f'value="{toyota.id}" selected' in html


def test_creating_a_model_redirects_back_to_that_brands_filtered_list(
        app, db, two_brands_with_models):
    toyota, _honda = two_brands_with_models
    client = _client(app, db)
    r = client.post("/master/vehicle-models/new",
                    data={"brand_id": toyota.id, "name": "Fortuner Test"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert f"brand_id={toyota.id}" in r.location
