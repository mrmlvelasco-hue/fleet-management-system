"""Stage 3D — every branch-scoped table carries its own company_id.

Michael's instruction (2026-10-29): Company replaces Branch across the
whole system, mobile included, and he asked for the migration and
transaction scripts.

Until now Company was stored on only four tables (branches, vehicles,
user_org_scopes, approval_instances) and everything else resolved it
THROUGH branch_id -- which was the right call while Branch was staying
(§20). Once Branch is being retired, inheritance through a column that
is going away is not a foundation: each branch-scoped table needs its
own company_id so the data survives Branch's removal.

This is deliberately ADDITIVE. Every new column is nullable and nothing
reads it yet, so Branch keeps working exactly as before and the change
is reversible until the backfill is run and reconciled.

Two distinct meanings, both intentional:
  * masters (driver, tire, battery, department, user, vendor) -- CURRENT
    organisational owner;
  * transactions (handover, checklist, MO origin/destination, approval
    task) -- a SNAPSHOT of org context when the document was raised,
    exactly the pattern ApprovalInstance.company_id already follows
    (D7), so a later reassignment never rewrites history.
"""
import pytest

from app.extensions import db


# (model import path, attribute) for every table that must now be able to
# answer "which Company?" on its own, without going through branch_id.
EXPECTED = [
    ("app.modules.master_data.driver.models", "Driver", "company_id"),
    ("app.modules.master_data.org.models", "Department", "company_id"),
    ("app.modules.master_data.tire.models", "Tire", "company_id"),
    ("app.modules.master_data.battery.models", "Battery", "company_id"),
    ("app.modules.user_management.models", "User", "company_id"),
    ("app.modules.transactions.vehicle_handover.models",
     "VehicleHandoverChecklist", "company_id"),
    ("app.modules.transactions.vehicle_checklist.models",
     "VehicleChecklist", "company_id"),
    ("app.core.approval.models", "ApprovalTask", "company_id"),
    # A transfer records where the vehicle came FROM and went TO; with
    # Branch retired both ends have to be Companies, or the Asset
    # Transfer Report loses its "From"/"To" (§2c of the impact analysis).
    ("app.modules.transactions.maintenance_order.models",
     "MaintenanceOrder", "origin_company_id"),
    ("app.modules.transactions.maintenance_order.models",
     "MaintenanceOrder", "destination_company_id"),
]


def _model(path, name):
    import importlib
    return getattr(importlib.import_module(path), name)


@pytest.mark.parametrize("path,name,attr", EXPECTED)
def test_model_has_a_company_column(db, path, name, attr):
    model = _model(path, name)
    assert hasattr(model, attr), f"{name}.{attr} is missing"


@pytest.mark.parametrize("path,name,attr", EXPECTED)
def test_company_column_is_nullable_and_points_at_org_companies(
        db, path, name, attr):
    """Nullable is what makes this additive: an unmapped legacy record is
    the normal state until the backfill runs, so adding these columns can
    never fail on existing data."""
    col = _model(path, name).__table__.columns[attr]
    assert col.nullable is True, f"{name}.{attr} must be nullable"
    targets = {fk.target_fullname for fk in col.foreign_keys}
    assert targets == {"org_companies.id"}, (
        f"{name}.{attr} should reference org_companies.id, got {targets}")


def test_vendor_company_coverage_table_exists(db):
    """Vendors serve MANY branches via the vendor_branches association
    table, not a column -- so the Company equivalent must also be an
    association, or a vendor serving three Companies could only record
    one."""
    from app.modules.master_data.vendor.models import vendor_companies
    cols = set(vendor_companies.columns.keys())
    assert cols == {"vendor_id", "company_id"}


def test_branch_columns_relaxed_only_when_company_took_over(db):
    """Branch was deliberately untouched while the company columns were
    being added -- that step was purely additive, so the system kept
    running on Branch while the new columns were backfilled and
    reconciled.

    It was relaxed only afterwards (migration e5b3d81c7a24, 2026-10-30),
    once Michael confirmed Company replaces Branch on the Vehicle and
    Driver forms. The ORDER matters: relaxing NOT NULL before Company
    was populated and proven would have allowed records with no org
    context at all, which are visible to every user.

    The foreign key itself stays, so existing rows keep their branch.
    """
    from app.modules.master_data.vehicle.models import Vehicle
    from app.modules.master_data.driver.models import Driver
    for model in (Vehicle, Driver):
        col = model.__table__.columns["branch_id"]
        assert col.nullable is True
        assert {fk.target_fullname for fk in col.foreign_keys} == {
            "branches.id"}, f"{model.__name__} lost its branch FK"
