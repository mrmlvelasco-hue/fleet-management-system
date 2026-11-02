"""MO Transaction Type -> Vehicle Master Data Update Mapping (config layer).

2026-11-02 spec: a Transaction Type can be configured to write its
proposed value to one Vehicle Master Data field on MO completion.
target_field must always be one of VEHICLE_FIELD_WHITELIST's keys --
never an arbitrary column name or SQL expression, checked here at save
time and again at apply time (see test_mo_vehicle_masterdata_update.py).
"""
import pytest

from app.modules.transactions.maintenance_order.service import (
    TransactionTypeService, InvalidFieldMappingError,
    VEHICLE_FIELD_WHITELIST)


@pytest.fixture()
def tt(db):
    return TransactionTypeService().create(
        code="ADM-CHG-COLOR-TEST", name="Change Color",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")


def test_whitelist_contains_the_spec_example_fields():
    for field in ("color", "engine_number", "chassis_number",
                  "vehicle_body_type"):
        assert field in VEHICLE_FIELD_WHITELIST


def test_a_new_transaction_type_has_no_mapping_by_default(db, tt):
    assert TransactionTypeService().get_vehicle_mapping(tt.id) is None


def test_set_vehicle_mapping_creates_an_active_mapping(db, tt):
    m = TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Vehicle Color",
        input_type="TEXT", is_required=True)
    assert m.target_field == "color"
    assert m.display_label == "New Vehicle Color"
    assert m.is_active is True
    fetched = TransactionTypeService().get_vehicle_mapping(tt.id)
    assert fetched.id == m.id


def test_set_vehicle_mapping_rejects_a_field_outside_the_whitelist(db, tt):
    with pytest.raises(InvalidFieldMappingError):
        TransactionTypeService().set_vehicle_mapping(
            tt.id, target_field="plate_number",  # not whitelisted
            display_label="New Plate", input_type="TEXT", is_required=True)


def test_set_vehicle_mapping_rejects_an_arbitrary_non_column_string(db, tt):
    """Never allow a user-configurable arbitrary DB column / SQL
    expression, per the spec's explicit security requirement."""
    with pytest.raises(InvalidFieldMappingError):
        TransactionTypeService().set_vehicle_mapping(
            tt.id, target_field="id; DROP TABLE vehicles;--",
            display_label="Hack", input_type="TEXT", is_required=True)


def test_calling_set_vehicle_mapping_again_updates_the_same_row(db, tt):
    first = TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Color",
        input_type="TEXT", is_required=True)
    second = TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="Updated Label",
        input_type="TEXT", is_required=False)
    assert second.id == first.id
    assert second.display_label == "Updated Label"
    assert second.is_required is False


def test_deactivate_vehicle_mapping_hides_it_from_active_lookup(db, tt):
    TransactionTypeService().set_vehicle_mapping(
        tt.id, target_field="color", display_label="New Color",
        input_type="TEXT", is_required=True)
    TransactionTypeService().deactivate_vehicle_mapping(tt.id)
    assert TransactionTypeService().get_vehicle_mapping(tt.id) is None


def test_unmapped_transaction_type_is_unaffected(db):
    """A Transaction Type nobody has configured a mapping for behaves
    exactly as before this feature existed (spec section 11)."""
    tt2 = TransactionTypeService().create(
        code="ADM-CHG-OWNER-TEST", name="Change of Ownership",
        order_category="OPERATIONAL", group="ADMINISTRATIVE")
    assert TransactionTypeService().get_vehicle_mapping(tt2.id) is None
