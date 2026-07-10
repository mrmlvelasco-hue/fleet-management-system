import pytest
from app.modules.system_admin.services.lookup_service import (
    LookupService, LookupRegistry, sync_lookups)
from app.modules.system_admin.models import Lookup


def test_get_by_type_returns_sorted_active(db):
    db.session.add_all([
        Lookup(lookup_type="FUEL_TYPE", code="DIESEL",
               description="Diesel", sort_order=2),
        Lookup(lookup_type="FUEL_TYPE", code="GASOLINE",
               description="Gasoline", sort_order=1),
        Lookup(lookup_type="FUEL_TYPE", code="ELECTRIC",
               description="Electric", sort_order=3, is_active=False),
    ])
    db.session.commit()
    items = LookupService().get_by_type("FUEL_TYPE")
    assert [i.code for i in items] == ["GASOLINE", "DIESEL"]


def test_get_by_type_empty_returns_empty(db):
    assert LookupService().get_by_type("NONEXISTENT") == []


def test_registry_sync_is_idempotent(db):
    reg = LookupRegistry()
    reg.register("VEHICLE_TYPE", "CAR", "Car", sort_order=1)
    reg.register("VEHICLE_TYPE", "TRUCK", "Truck", sort_order=2)
    sync_lookups(reg)
    sync_lookups(reg)
    db.session.commit()
    assert Lookup.query.filter_by(
        lookup_type="VEHICLE_TYPE").count() == 2


def test_create_lookup(db):
    LookupService().create("FUEL_TYPE", "LPG", "LPG Gas", sort_order=4)
    assert Lookup.query.filter_by(code="LPG").count() == 1


def test_deactivate_lookup(db):
    svc = LookupService()
    item = svc.create("FUEL_TYPE", "HYBRID", "Hybrid", sort_order=5)
    svc.deactivate(item.id)
    assert Lookup.query.get(item.id).is_active is False
