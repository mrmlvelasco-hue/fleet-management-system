"""Custom Report Builder v315: Fuel Transactions and Purchase
Requisitions sources, AND/OR filters, new operators, grouping, totals,
paging, field-level permissions, organisational scope and exports.

Every scenario uses real rows built through the models -- no fabricated
report data -- and checks the numbers the person would read.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.core.reporting.report_builder import (
    DATA_SOURCES, ReportBuilderError, catalog, json_result, run_report,
    to_csv, to_xlsx)
from app.core.security.password import hash_password
from app.extensions import db
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService


class Perms:
    """A user stand-in holding exactly these permission codes."""

    def __init__(self, *codes, uid=None):
        self.codes = set(codes)
        self.id = uid
        self.restrict_to_assigned_vehicles = False

    def has_permission(self, code):
        return code in self.codes


@pytest.fixture()
def world(db):
    from app.modules.master_data.driver.models import Driver
    from app.modules.master_data.org.models import Department
    from app.modules.master_data.vendor.models import Vendor
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest, PurchaseRequestLine)
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.core.approval.models import ApprovalAction, ApprovalInstance
    from app.modules.user_management.models import User

    vt = VehicleTypeService().create(code="LV-RB", name="Light",
                                     category="LIGHT")
    carmona = BranchService().create(code="CRM-RB", name="Carmona")
    manila = BranchService().create(code="MNL-RB", name="Manila")
    v1 = VehicleService().create(vehicle_type_id=vt.id, brand="Toyota",
                                 model="Hilux", year=2023,
                                 branch_id=carmona.id,
                                 conduction_number="RB-1",
                                 plate_number="RB1")
    v2 = VehicleService().create(vehicle_type_id=vt.id, brand="Isuzu",
                                 model="D-Max", year=2022,
                                 branch_id=manila.id,
                                 conduction_number="RB-2",
                                 plate_number="RB2")
    driver = Driver(first_name="Juan", middle_name="Santos",
                    last_name="Dela Cruz", employee_number="E-77",
                    position="Driver")
    petron = Vendor(code="PET-RB", name="Petron Carmona")
    approver = User(username="approver_rb", email="ap@e.com",
                    first_name="Ana", last_name="Reyes",
                    password_hash=hash_password("secret123"))
    requester = User(username="req_rb", email="rq@e.com",
                     first_name="Rico", last_name="Lim",
                     password_hash=hash_password("secret123"))
    db.session.add_all([driver, petron, approver, requester])
    db.session.flush()
    dept = Department(code="MNT-RB", name="Maintenance",
                      branch_id=carmona.id)
    db.session.add(dept)
    db.session.flush()

    fills = [
        (v1, datetime(2026, 11, 1, 8, 30), "10.500", "60.1234", "631.30",
         "DIESEL", "Petron Carmona"),
        (v1, datetime(2026, 11, 30, 23, 15), "20.250", "61.0000",
         "1235.25", "DIESEL", "Shell Silang"),
        (v2, datetime(2026, 11, 15, 12, 0), "30.000", "65.5000", "1965.00",
         "GASOLINE", None),
        (v2, datetime(2026, 12, 1, 0, 5), "5.000", "66.0000", "330.00",
         "GASOLINE", None),
    ]
    for i, (veh, when, litres, ppl, amount, ftype, station) in enumerate(
            fills):
        db.session.add(FuelTransaction(
            document_number=f"FT-RB-{i + 1}", vehicle_id=veh.id,
            driver_id=driver.id if veh is v1 else None,
            vendor_id=petron.id if station == "Petron Carmona" else None,
            transaction_date=when, litres=Decimal(litres),
            price_per_litre=Decimal(ppl), total_amount=Decimal(amount),
            fuel_type=ftype, station=station, reference_number=f"R{i}"))

    # PR with three item lines, generated from an MO on v1.
    instance = ApprovalInstance(document_type_id=1,
                                reference_table="purchase_requests",
                                reference_id=0, status="APPROVED")
    db.session.add(instance)
    db.session.flush()
    pr1 = PurchaseRequest(document_number="PR-RB-1",
                          description="Brake parts", amount=Decimal("900"),
                          status="APPROVED", requested_by=requester.id,
                          department_id=dept.id, vendor_id=petron.id,
                          needed_by_date=date(2026, 11, 20),
                          approval_instance_id=instance.id)
    pr2 = PurchaseRequest(document_number="PR-RB-2",
                          description="Wipers", amount=Decimal("150"),
                          status="DRAFT", requested_by=requester.id)
    db.session.add_all([pr1, pr2])
    db.session.flush()
    instance.reference_id = pr1.id
    for desc, qty, cost in (("Brake pad", 2, 300), ("Brake fluid", 1, 200),
                            ("Labor", 1, 100)):
        pr1.lines.append(PurchaseRequestLine(
            item_description=desc, quantity=qty, unit_cost=cost,
            line_total=qty * cost))
    pr2.lines.append(PurchaseRequestLine(item_description="Wiper blade",
                                         quantity=1, unit_cost=150,
                                         line_total=150))
    db.session.add(ApprovalAction(instance_id=instance.id, level_number=1,
                                  action="APPROVE", acted_by=approver.id,
                                  acted_at=datetime(2026, 11, 5, 9, 0)))
    mo = MaintenanceOrder(document_number="MO-RB-1", vehicle_id=v1.id,
                          order_category="MAINTENANCE",
                          scheduled_date=date(2026, 11, 2),
                          status="IN_PROGRESS", purchase_request_id=pr1.id)
    db.session.add(mo)
    db.session.add(TripTicket(document_number="TT-RB-1", vehicle_id=v1.id,
                              destination="Calamba", purpose="Delivery",
                              departure_datetime=datetime(2026, 11, 3, 7),
                              odometer_out=1000, odometer_in=1120))
    db.session.commit()
    return {"carmona": carmona, "manila": manila, "v1": v1, "v2": v2,
            "pr1": pr1, "pr2": pr2}


# ── every field of every source actually runs ──────────────────────────

@pytest.mark.parametrize("source_key", list(DATA_SOURCES))
def test_every_field_of_every_source_runs(world, source_key):
    """A field pointing at a column that does not exist used to show
    blanks forever (Trip Tickets' trip_date). Selecting, sorting,
    grouping and filtering on each field must all work."""
    source = DATA_SOURCES[source_key]
    keys = list(source.fields)
    run_report(source_key, keys)
    for key, f in source.fields.items():
        run_report(source_key, [keys[0]], sort_key=key, group_by=key)
        run_report(source_key, [keys[0]],
                   filters=[{"field": key, "op": "not_empty"}],
                   strict=True)


def test_trip_date_now_shows_the_departure(world):
    r = run_report("trip_tickets", ["document_number", "trip_date",
                                    "distance_km"])
    assert r["rows"][0][1] == datetime(2026, 11, 3, 7)
    assert r["rows"][0][2] == 120


# ── Fuel Transactions ──────────────────────────────────────────────────

FUEL_COLS = ["document_number", "transaction_date", "plate_number",
             "driver_name", "fuel_type", "litres", "price_per_litre",
             "total_amount", "station", "supplier_name", "branch_name"]


def test_fuel_report_reads_real_values_with_precision(world):
    r = run_report("fuel_transactions", FUEL_COLS, sort_key="document_number")
    first = dict(zip(FUEL_COLS, r["rows"][0]))
    assert first["plate_number"] == "RB1"
    assert first["driver_name"] == "Juan S. Dela Cruz"
    assert first["litres"] == Decimal("10.500")
    assert first["price_per_litre"] == Decimal("60.1234")
    assert first["supplier_name"] == "Petron Carmona"
    assert first["branch_name"] == "Carmona"
    assert r["totals"]["litres"] == Decimal("65.750")
    assert r["totals"]["total_amount"] == Decimal("4161.55")
    out = json_result(r)
    assert out["rows"][0][FUEL_COLS.index("price_per_litre")] == "60.1234"
    assert out["totals"]["total_amount"] == "4161.55"


def test_date_range_on_a_date_time_column_includes_the_whole_last_day(world):
    """Nov 1 - Nov 30 must include the 23:15 fill on the 30th and
    exclude 00:05 on Dec 1."""
    r = run_report("fuel_transactions", ["document_number"],
                   filters=[{"field": "transaction_date", "op": "between",
                             "value": "2026-11-01", "value2": "2026-11-30"}],
                   strict=True)
    assert sorted(x[0] for x in r["rows"]) == ["FT-RB-1", "FT-RB-2",
                                               "FT-RB-3"]


def test_and_or_filters(world):
    flt = [{"field": "fuel_type", "op": "eq", "value": "DIESEL"},
           {"field": "branch_name", "op": "eq", "value": "Manila"}]
    both = run_report("fuel_transactions", ["document_number"], filters=flt,
                      filter_logic="AND", strict=True)
    either = run_report("fuel_transactions", ["document_number"],
                        filters=flt, filter_logic="OR", strict=True)
    assert both["row_count"] == 0
    assert either["row_count"] == 4


def test_not_contains_keeps_empty_values_and_is_empty_finds_them(world):
    nc = run_report("fuel_transactions", ["document_number"],
                    filters=[{"field": "station", "op": "not_contains",
                              "value": "petron"}], strict=True)
    assert nc["row_count"] == 3
    empty = run_report("fuel_transactions", ["document_number"],
                       filters=[{"field": "station", "op": "is_empty"}],
                       strict=True)
    assert empty["row_count"] == 2


def test_group_by_branch_gives_subtotals(world):
    r = run_report("fuel_transactions", ["document_number", "total_amount"],
                   group_by="branch_name", sort_key="transaction_date",
                   sort_dir="desc")
    assert r["group_values"] == ["Carmona", "Carmona", "Manila", "Manila"]
    groups = {g["value"]: g for g in r["groups"]}
    assert groups["Carmona"]["count"] == 2
    assert groups["Carmona"]["totals"]["total_amount"] == Decimal("1866.55")
    assert groups["Manila"]["totals"]["total_amount"] == Decimal("2295.00")
    # sorted within each group, newest first
    assert [x[0] for x in r["rows"]][:2] == ["FT-RB-2", "FT-RB-1"]


def test_paging_reports_the_total(world):
    p1 = run_report("fuel_transactions", ["document_number"], page=1,
                    per_page=3, sort_key="document_number")
    p2 = run_report("fuel_transactions", ["document_number"], page=2,
                    per_page=3, sort_key="document_number")
    assert p1["total"] == 4 and p1["pages"] == 2
    assert [r[0] for r in p1["rows"]] == ["FT-RB-1", "FT-RB-2", "FT-RB-3"]
    assert [r[0] for r in p2["rows"]] == ["FT-RB-4"]
    # totals describe the whole report, not one page
    assert p2["totals"] == {}


def test_no_results_is_an_empty_report_not_an_error(world):
    r = run_report("fuel_transactions", ["document_number", "litres"],
                   filters=[{"field": "plate_number", "op": "eq",
                             "value": "NOPE"}], page=1, per_page=50)
    assert r["rows"] == [] and r["total"] == 0 and r["totals"] == {}


def test_fuel_scope_follows_the_fuel_list(world):
    from app.modules.user_management.models import User
    from app.modules.user_management.org_scope_service import (
        UserOrgScopeService)
    u = User(username="fscope_rb", email="f@e.com",
             password_hash=hash_password("secret123"))
    db.session.add(u)
    db.session.commit()
    UserOrgScopeService().assign(u.id, scope_type="BRANCH",
                                 branch_id=world["carmona"].id)
    user = Perms("fuel.view", uid=u.id)
    r = run_report("fuel_transactions", ["plate_number"], user=user)
    assert {x[0] for x in r["rows"]} == {"RB1"}


# ── Purchase Requisitions ──────────────────────────────────────────────

def test_pr_header_report_is_one_row_per_pr(world):
    r = run_report("purchase_requisitions",
                   ["document_number", "amount", "line_count", "lines_total",
                    "requester_name", "department_name", "branch_name",
                    "supplier_name", "approver", "approval_date",
                    "work_order_number", "plate_number"],
                   sort_key="document_number")
    assert r["row_count"] == 2
    pr1 = r["rows"][0]
    assert pr1[0] == "PR-RB-1"
    assert pr1[1] == Decimal("900.00")
    assert pr1[2] == 3 and Decimal(str(pr1[3])) == Decimal("900")
    assert pr1[4] == "Rico Lim"
    assert pr1[5:8] == ["Maintenance", "Carmona", "Petron Carmona"]
    assert pr1[8] == "Ana Reyes"
    assert pr1[9] == datetime(2026, 11, 5, 9, 0)
    assert pr1[10:12] == ["MO-RB-1", "RB1"]
    assert r["totals"]["amount"] == Decimal("1050.00")


def test_item_lines_do_not_duplicate_the_pr_total(world):
    """Scenario 14: three lines on PR-RB-1 must not triple its 900."""
    r = run_report("purchase_requisitions",
                   ["document_number", "item_description", "line_total",
                    "amount"], sort_key="document_number")
    assert r["row_count"] == 4
    amounts = [row[3] for row in r["rows"]]
    assert amounts == [Decimal("900.00"), None, None, Decimal("150.00")]
    assert r["totals"]["amount"] == Decimal("1050.00")
    assert r["totals"]["line_total"] == Decimal("1050.00")


def test_grouped_pr_lines_still_count_the_header_once(world):
    r = run_report("purchase_requisitions",
                   ["item_description", "amount"], group_by="document_number")
    groups = {g["value"]: g for g in r["groups"]}
    assert groups["PR-RB-1"]["count"] == 3
    assert groups["PR-RB-1"]["totals"]["amount"] == Decimal("900.00")


def test_filtering_on_an_item_keeps_one_row_per_pr(world):
    """Asking 'PRs with a brake item' must not list PR-RB-1 twice."""
    r = run_report("purchase_requisitions", ["document_number", "amount"],
                   filters=[{"field": "item_description", "op": "contains",
                             "value": "brake"}], strict=True)
    assert r["rows"] == [["PR-RB-1", Decimal("900.00")]]


def test_pr_amount_filter(world):
    r = run_report("purchase_requisitions", ["document_number"],
                   filters=[{"field": "amount", "op": "gt", "value": "500"}],
                   strict=True)
    assert r["rows"] == [["PR-RB-1"]]


# ── permissions and validation ─────────────────────────────────────────

def test_unauthorized_field_is_refused_and_hidden_from_the_catalog(world):
    """Scenario 15: Work Order fields on a PR need maintenanceorder.view."""
    user = Perms("purchaserequest.view")
    with pytest.raises(ReportBuilderError) as exc:
        run_report("purchase_requisitions",
                   ["document_number", "work_order_number"], user=user)
    assert "maintenanceorder.view" in str(exc.value)
    with pytest.raises(ReportBuilderError):
        run_report("purchase_requisitions", ["document_number"], user=user,
                   filters=[{"field": "plate_number", "op": "eq",
                             "value": "RB1"}])
    cat = catalog(user)
    keys = [f["key"] for f in cat["sources"][0]["fields"]]
    assert cat["sources"][0]["key"] == "purchase_requisitions"
    assert "work_order_number" not in keys and "document_number" in keys


def test_catalog_lists_only_readable_sources(world):
    cat = catalog(Perms("fuel.view"))
    assert [s["key"] for s in cat["sources"]] == ["fuel_transactions"]
    src = cat["sources"][0]
    assert "Fuel" in src["categories"]
    assert all(f["category"] in src["categories"] for f in src["fields"])


@pytest.mark.parametrize("flt,msg", [
    ({"field": "litres", "op": "contains", "value": "1"}, "cannot be used"),
    ({"field": "litres", "op": "gt", "value": "abc"}, "not a number"),
    ({"field": "litres", "op": "between", "value": "1"}, "both values"),
    ({"field": "no_such", "op": "eq", "value": "1"}, "not available"),
    ({"field": "transaction_date", "op": "gte", "value": "31/31/2026"},
     "valid date"),
])
def test_invalid_filters_give_a_clear_message(world, flt, msg):
    with pytest.raises(ReportBuilderError) as exc:
        run_report("fuel_transactions", ["document_number"], filters=[flt],
                   strict=True)
    assert msg in str(exc.value)


def test_saved_reports_keep_tolerating_half_filled_filter_rows(world):
    """The Flask form saves rows with no value; they were ignored before
    and still are when a saved report runs."""
    r = run_report("fuel_transactions", ["document_number"],
                   filters=[{"field": "station", "op": "eq", "value": ""}])
    assert r["row_count"] == 4


# ── exports ────────────────────────────────────────────────────────────

def test_csv_is_exact_and_carries_the_group_column(world):
    r = run_report("fuel_transactions", ["document_number", "litres"],
                   group_by="branch_name", sort_key="document_number")
    text = to_csv(r).decode("utf-8-sig").splitlines()
    assert text[0] == "Branch,Fuel Transaction No.,Quantity (L)"
    assert text[1] == "Carmona,FT-RB-1,10.500"


def test_xlsx_writes_numbers_subtotals_and_a_total(world):
    from io import BytesIO
    from openpyxl import load_workbook
    r = run_report("fuel_transactions", ["document_number", "total_amount"],
                   group_by="branch_name", sort_key="document_number")
    ws = load_workbook(BytesIO(to_xlsx(r, "Fuel"))).active
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[1][0].startswith("Branch: Carmona")
    assert values[2] == ["FT-RB-1", 631.3]
    assert ["Subtotal", 1866.55] in values
    assert values[-1] == ["Total", 4161.55]
    assert ws.cell(row=3, column=2).number_format == "#,##0.00"
