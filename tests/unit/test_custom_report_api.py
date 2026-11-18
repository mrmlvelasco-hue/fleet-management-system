"""/api/v1/admin/custom-reports, v315.

The scenarios from the enhancement brief that need the API: create and
save reports for every source, reopen and edit IN PLACE, preview with
paging, CSV/XLSX export, schedules, branch scope, and saved reports from
before v315 still opening, running, editing and exporting.
"""
import json
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import pytest

from app.core.security.password import hash_password
from app.core.security.registry import sync_permissions
from app.extensions import db
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.system_admin.models import CustomReport, ScheduledReport
from app.modules.system_admin.services.custom_report_service import (
    CustomReportService)
from app.modules.user_management.models import Permission, Role, User
from app.modules.user_management.org_scope_service import UserOrgScopeService

ALL = ["customreport.view", "customreport.manage", "vehicle.view",
       "maintenanceorder.view", "vehicleregistration.view",
       "tripticket.view", "fuel.view", "purchaserequest.view"]


def _user(name, codes, branch=None):
    role = Role(name=f"role-{name}")
    role.permissions = Permission.query.filter(Permission.code.in_(codes)).all()
    u = User(username=name, email=f"{name}@e.com", first_name=name.title(),
             last_name="Tester", password_hash=hash_password("secret123"),
             is_active=True)
    u.roles = [role]
    db.session.add_all([role, u])
    db.session.commit()
    if branch is not None:
        UserOrgScopeService().assign(u.id, scope_type="BRANCH",
                                     branch_id=branch.id)
    return u


def _hdr(client, name):
    r = client.post("/api/v1/auth/token",
                    json={"username": name, "password": "secret123"})
    return {"Authorization": "Bearer "
            + json.loads(r.get_data(as_text=True))["access_token"]}


def _body(r):
    return json.loads(r.get_data(as_text=True))


@pytest.fixture()
def env(db):
    from app.modules.transactions.fuel.models import FuelTransaction
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder)
    from app.modules.transactions.purchase_request.models import (
        PurchaseRequest, PurchaseRequestLine)
    from app.modules.transactions.trip_ticket.models import TripTicket
    from app.modules.transactions.vehicle_registration.models import (
        VehicleRegistration)
    sync_permissions()
    vt = VehicleTypeService().create(code="LV-API", name="Light",
                                     category="LIGHT")
    carmona = BranchService().create(code="CRM-API", name="Carmona")
    manila = BranchService().create(code="MNL-API", name="Manila")
    vs = []
    for i, br in enumerate((carmona, manila)):
        vs.append(VehicleService().create(
            vehicle_type_id=vt.id, brand="Toyota", model="Hilux",
            year=2023, branch_id=br.id, conduction_number=f"API-{i}",
            plate_number=f"API{i}"))
    for i, v in enumerate(vs):
        db.session.add(FuelTransaction(
            document_number=f"FT-API-{i}", vehicle_id=v.id,
            transaction_date=datetime(2026, 11, 10 + i), litres=Decimal("10"),
            price_per_litre=Decimal("60"), total_amount=Decimal("600"),
            fuel_type="DIESEL"))
        db.session.add(MaintenanceOrder(
            document_number=f"MO-API-{i}", vehicle_id=v.id,
            order_category="MAINTENANCE", scheduled_date=date(2026, 11, 1),
            status="COMPLETED", actual_cost=Decimal("1000")))
        db.session.add(VehicleRegistration(
            document_number=f"VR-API-{i}", vehicle_id=v.id,
            registration_type="RENEWAL", expiry_date=date(2027, 1, 1)))
        db.session.add(TripTicket(
            document_number=f"TT-API-{i}", vehicle_id=v.id,
            destination="Laguna", purpose="Delivery",
            departure_datetime=datetime(2026, 11, 2, 8)))
    pr = PurchaseRequest(document_number="PR-API-1", amount=Decimal("300"),
                         status="APPROVED", description="Filters")
    db.session.add(pr)
    db.session.flush()
    for desc in ("Oil filter", "Air filter"):
        pr.lines.append(PurchaseRequestLine(item_description=desc,
                                            quantity=1, unit_cost=150,
                                            line_total=150))
    db.session.commit()
    admin = _user("rbadmin", ALL + ["reportconfig.view"])
    scoped = _user("rbscoped", ALL, branch=carmona)
    return {"admin": admin, "scoped": scoped, "carmona": carmona,
            "manila": manila}


SOURCES = [
    ("vehicles", ["plate_number", "branch_name"]),
    ("maintenance_orders", ["document_number", "actual_cost", "branch_name"]),
    ("vehicle_registrations", ["document_number", "expiry_date"]),
    ("trip_tickets", ["document_number", "trip_date"]),
    ("fuel_transactions", ["document_number", "litres", "total_amount"]),
    ("purchase_requisitions", ["document_number", "item_description",
                               "line_total", "amount"]),
]


@pytest.mark.parametrize("source,fields", SOURCES)
def test_create_save_and_run_a_report_for_each_source(app, client, env,
                                                      source, fields):
    """Scenarios 1-6."""
    h = _hdr(client, "rbadmin")
    r = client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": f"My {source}", "data_source": source, "fields": fields})
    assert r.status_code == 201, r.get_data(as_text=True)
    rid = _body(r)["id"]
    run = _body(client.get(f"/api/v1/admin/custom-reports/{rid}/run"
                           f"?page=1&per_page=10", headers=h))
    assert [c["key"] for c in run["columns"]] == fields
    assert run["total"] >= 1 and run["rows"]


def test_catalog_is_categorised_and_has_operators(app, client, env):
    body = _body(client.get("/api/v1/admin/custom-reports",
                            headers=_hdr(client, "rbadmin")))
    keys = [s["key"] for s in body["sources"]]
    assert keys == ["vehicles", "maintenance_orders",
                    "vehicle_registrations", "trip_tickets",
                    "fuel_transactions", "purchase_requisitions"]
    fuel = body["sources"][4]
    assert fuel["categories"][0] == "Transaction"
    assert {"key", "label", "kind", "category"} <= set(fuel["fields"][0])
    assert "between" in body["operators_by_kind"]["date"]
    assert "contains" not in body["operators_by_kind"]["number"]


def test_preview_pages_with_totals_and_explains_bad_filters(app, client,
                                                            env):
    h = _hdr(client, "rbadmin")
    payload = {"data_source": "fuel_transactions",
               "fields": ["document_number", "total_amount"],
               "sort_key": "document_number", "page": 2, "per_page": 1}
    body = _body(client.post("/api/v1/admin/custom-reports/preview",
                             headers=h, json=payload))
    assert body["ok"] and body["total"] == 2 and body["pages"] == 2
    assert body["rows"] == [["FT-API-1", "600.00"]]
    assert body["totals"] == {"total_amount": "1200.00"}

    bad = client.post("/api/v1/admin/custom-reports/preview", headers=h,
                      json={**payload, "filters": [
                          {"field": "litres", "op": "gt", "value": "ten"}]})
    assert bad.status_code == 400
    assert "not a number" in _body(bad)["message"]


def test_reopen_and_edit_keeps_the_same_report(app, client, env):
    """Scenario 12. The screen used to POST a NEW report on every edit."""
    h = _hdr(client, "rbadmin")
    rid = _body(client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Fuel", "data_source": "fuel_transactions",
        "fields": ["document_number"]}))["id"]
    r = client.put(f"/api/v1/admin/custom-reports/{rid}", headers=h, json={
        "name": "Fuel by branch", "data_source": "fuel_transactions",
        "fields": ["branch_name", "document_number", "total_amount"],
        "filters": [{"field": "fuel_type", "op": "eq", "value": "DIESEL"},
                    {"field": "branch_name", "op": "eq", "value": "X"}],
        "filter_logic": "OR", "group_by": "branch_name",
        "sort_key": "transaction_date", "sort_dir": "desc"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert CustomReport.query.filter_by(is_active=True).count() == 1
    got = _body(client.get(f"/api/v1/admin/custom-reports/{rid}", headers=h))
    assert got["fields"] == ["branch_name", "document_number",
                             "total_amount"]
    assert got["filter_logic"] == "OR" and got["group_by"] == "branch_name"
    run = _body(client.get(f"/api/v1/admin/custom-reports/{rid}/run",
                           headers=h))
    assert [g["value"] for g in run["groups"]] == ["Carmona", "Manila"]


def test_saving_an_invalid_definition_is_refused(app, client, env):
    h = _hdr(client, "rbadmin")
    r = client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Bad", "data_source": "fuel_transactions",
        "fields": ["document_number"],
        "filters": [{"field": "transaction_date", "op": "between",
                     "value": "2026-11-01"}]})
    assert r.status_code == 400
    assert "both values" in _body(r)["message"]
    assert CustomReport.query.count() == 0


def test_export_csv_and_xlsx(app, client, env):
    """Scenario 11."""
    from openpyxl import load_workbook
    h = _hdr(client, "rbadmin")
    rid = _body(client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Fuel export", "data_source": "fuel_transactions",
        "fields": ["document_number", "total_amount"],
        "sort_key": "document_number"}))["id"]
    csv = client.get(f"/api/v1/admin/custom-reports/{rid}/export?format=csv",
                     headers=h)
    assert csv.status_code == 200 and csv.mimetype == "text/csv"
    lines = csv.data.decode("utf-8-sig").splitlines()
    assert lines == ["Fuel Transaction No.,Total Amount",
                     "FT-API-0,600.00", "FT-API-1,600.00"]
    xlsx = client.get(f"/api/v1/admin/custom-reports/{rid}/export"
                      f"?format=xlsx", headers=h)
    ws = load_workbook(BytesIO(xlsx.data)).active
    assert ws.cell(row=2, column=2).value == 600
    assert ws.cell(row=4, column=1).value == "Total"
    assert client.get(f"/api/v1/admin/custom-reports/{rid}/export"
                      f"?format=pdf", headers=h).status_code == 400


def test_branch_scope_applies_to_every_source(app, client, env):
    """Scenario 13: a Carmona-scoped user sees only Carmona's rows."""
    h = _hdr(client, "rbscoped")
    for source, field in (("vehicles", "plate_number"),
                          ("maintenance_orders", "document_number"),
                          ("vehicle_registrations", "document_number"),
                          ("trip_tickets", "document_number"),
                          ("fuel_transactions", "document_number")):
        body = _body(client.post("/api/v1/admin/custom-reports/preview",
                                 headers=h, json={"data_source": source,
                                                  "fields": [field]}))
        assert body["total"] == 1, source
        assert body["rows"][0][0].endswith("0"), source


def test_unauthorized_field_via_api(app, client, env):
    """Scenario 15."""
    _user("rbnomo", ["customreport.view", "customreport.manage",
                     "purchaserequest.view"])
    h = _hdr(client, "rbnomo")
    body = _body(client.get("/api/v1/admin/custom-reports", headers=h))
    assert [s["key"] for s in body["sources"]] == ["purchase_requisitions"]
    keys = {f["key"] for f in body["sources"][0]["fields"]}
    assert "work_order_number" not in keys
    r = client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Sneaky", "data_source": "purchase_requisitions",
        "fields": ["document_number", "work_order_number"]})
    assert r.status_code == 400


def test_schedule_is_saved_delivered_and_stopped(app, client, env):
    from app.modules.system_admin.services.scheduled_report_service import (
        ScheduledReportService)
    h = _hdr(client, "rbadmin")
    r = client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Monthly fuel", "data_source": "fuel_transactions",
        "fields": ["document_number"],
        "schedule": {"frequency": "MONTHLY", "format": "csv",
                     "recipients": "fleet@x.com, finance@x.com"}})
    body = _body(r)
    assert body["schedule"]["frequency"] == "MONTHLY"
    assert body["schedule"]["format"] == "csv"
    sched = ScheduledReport.query.filter_by(
        report_code=f"CUSTOM:{body['id']}").one()
    sched.next_run_at = datetime(2000, 1, 1)
    db.session.commit()
    with patch("app.modules.system_admin.services.email_config_service"
               ".EmailSenderService.send") as sender:
        sent, failed = ScheduledReportService().run_due(only_id=sched.id)
    assert (sent, failed) == (1, 0)
    att = sender.call_args.kwargs["attach_files"][0]
    assert att["filename"] == "Monthly fuel.csv"
    assert att["mime_type"] == "text/csv"
    assert sender.call_count == 2

    client.put(f"/api/v1/admin/custom-reports/{body['id']}", headers=h, json={
        "name": "Monthly fuel", "data_source": "fuel_transactions",
        "fields": ["document_number"], "schedule": {"frequency": "NONE"}})
    assert _body(client.get(f"/api/v1/admin/custom-reports/{body['id']}",
                            headers=h))["schedule"] is None


def test_report_saved_before_v315_still_works(app, client, env):
    """A definition stored the old way (no filter_logic/group_by sent,
    legacy filters, half-filled rows from the Flask form) still opens,
    runs, exports and can be edited from React."""
    old = CustomReportService().create(
        name="Legacy MO", data_source="maintenance_orders",
        fields=["document_number", "plate_number", "actual_cost"],
        filters=[{"field": "status", "op": "eq", "value": "COMPLETED"},
                 {"field": "vendor_name", "op": "eq", "value": ""}],
        sort_key="document_number")
    h = _hdr(client, "rbadmin")
    got = _body(client.get(f"/api/v1/admin/custom-reports/{old.id}",
                           headers=h))
    assert got["filter_logic"] == "AND" and got["group_by"] is None
    run = _body(client.get(f"/api/v1/admin/custom-reports/{old.id}/run",
                           headers=h))
    assert run["total"] == 2
    assert client.get(f"/api/v1/admin/custom-reports/{old.id}/export",
                      headers=h).status_code == 200
    r = client.put(f"/api/v1/admin/custom-reports/{old.id}", headers=h,
                   json={**got, "filters": got["filters"][:1]})
    assert r.status_code == 200
    assert _body(r)["id"] == old.id


def test_flask_form_edit_keeps_or_logic_and_grouping(app, client, env):
    svc = CustomReportService()
    rep = svc.create(name="R", data_source="fuel_transactions",
                     fields=["document_number"], filter_logic="OR",
                     group_by="branch_name")
    svc.update(rep.id, name="R2", data_source="fuel_transactions",
               fields=["document_number"])
    assert rep.filter_logic == "OR" and rep.group_by == "branch_name"


def test_deleting_a_report_stops_its_schedule(app, client, env):
    h = _hdr(client, "rbadmin")
    rid = _body(client.post("/api/v1/admin/custom-reports", headers=h, json={
        "name": "Weekly", "data_source": "vehicles",
        "fields": ["plate_number"],
        "schedule": {"frequency": "WEEKLY", "format": "xlsx",
                     "recipients": "a@x.com"}}))["id"]
    client.post(f"/api/v1/admin/custom-reports/{rid}/delete", headers=h)
    assert ScheduledReport.query.filter_by(
        report_code=f"CUSTOM:{rid}", is_active=True).count() == 0
    assert client.get(f"/api/v1/admin/custom-reports/{rid}",
                      headers=h).status_code == 404
