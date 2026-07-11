from app.modules.system_admin.services.lookup_service import LookupService


def test_get_by_type_with_fallback_uses_registry_when_db_empty(db):
    items = LookupService().get_by_type_with_fallback("VEHICLE_CATEGORY")
    codes = {i.code for i in items}
    assert "LIGHT" in codes and "HEAVY" in codes


def test_get_by_type_with_fallback_prefers_db_rows_once_seeded(db):
    from app.modules.system_admin.services.lookup_service import sync_lookups
    sync_lookups()
    db.session.commit()
    LookupService().create("VEHICLE_CATEGORY", "ELECTRIC_LIGHT",
                          "Electric Light Vehicle", sort_order=5)
    items = LookupService().get_by_type_with_fallback("VEHICLE_CATEGORY")
    codes = {i.code for i in items}
    assert "ELECTRIC_LIGHT" in codes  # admin-added value now visible
