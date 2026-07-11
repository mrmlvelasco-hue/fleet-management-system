from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.vendor.service import VendorService
from app.modules.master_data.tire.service import TireService
from app.modules.master_data.battery.service import BatteryService
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.maintenance_config.service import (
    PMScheduleService, PMScopeTemplateService)


def _login(client, db, *, codes=()):
    role = Role(name="MaintRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission(code=code, module=m, action=a)
        db.session.add(p)
        role.permissions.append(p)
    u = User(username="priya", email="priya@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "priya", "password": "pw123456"})
    return u


def _seed_common(db):
    branch = BranchService().create(code="BR-3B", name="3B Branch")
    vt = VehicleTypeService().create(code="LV-3B", name="Light", category="LIGHT")
    mt = MaintenanceTypeService().create(
        code="PMS-3B", name="5,000 KM PMS", category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Fortuner", year=2024,
        branch_id=branch.id, conduction_number="3B-000")
    vendor = VendorService().create(code="VEN-3B", name="Auto Shop 3B")
    dt = DocumentTypeService().create(code="MO", name="Maintenance Order",
                                      requires_approval=False, auto_numbering=True)
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    return branch, vt, mt, vehicle, vendor


# ---------- PM Config UI ----------

def test_pmschedule_list_403_without_permission(client, db):
    _login(client, db)
    assert client.get("/admin/pm-schedules").status_code == 403


def test_pmschedule_pages_render(client, db):
    _login(client, db, codes=["pmschedule.view", "pmschedule.create"])
    assert client.get("/admin/pm-schedules").status_code == 200
    assert client.get("/admin/pm-schedules/new").status_code == 200


def test_pmscope_pages_render(client, db):
    _login(client, db, codes=["pmscopetemplate.view", "pmscopetemplate.create"])
    assert client.get("/admin/pm-scope-templates").status_code == 200
    assert client.get("/admin/pm-scope-templates/new").status_code == 200


# ---------- Maintenance Order UI + checklist flow ----------

def test_maintenanceorder_list_renders(client, db):
    _login(client, db, codes=["maintenanceorder.view"])
    assert client.get("/transactions/maintenance-orders").status_code == 200


def test_maintenanceorder_full_flow_with_checklist(client, db):
    user = _login(client, db, codes=[
        "maintenanceorder.view", "maintenanceorder.create",
        "maintenanceorder.update", "maintenanceorder.print"])
    branch, vt, mt, vehicle, vendor = _seed_common(db)
    scope = PMScopeTemplateService().create(
        maintenance_type_id=mt.id, name="5K Scope 3B", items=[
            {"activity_code": "OIL", "activity_description": "Change Oil", "sort_order": 1},
        ])

    resp = client.post("/transactions/maintenance-orders/new", data={
        "vehicle_id": str(vehicle.id), "maintenance_type_id": str(mt.id),
        "scope_template_id": str(scope.id),
        "scheduled_date": "2026-07-20",
        "odometer_at_service": "5000",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.maintenance_order.models import (
        MaintenanceOrder, MaintenanceChecklistItem)
    order = MaintenanceOrder.query.first()
    assert order is not None
    assert len(order.checklist_items) == 1

    client.post(f"/transactions/maintenance-orders/{order.id}/submit",
               follow_redirects=True)
    client.post(f"/transactions/maintenance-orders/{order.id}/start-work",
               follow_redirects=True)

    item = order.checklist_items[0]
    client.post(
        f"/transactions/maintenance-orders/{order.id}/checklist/{item.id}/toggle",
        data={"done": "1"}, follow_redirects=True)
    assert db.session.get(MaintenanceChecklistItem, item.id).is_done is True

    complete_resp = client.post(
        f"/transactions/maintenance-orders/{order.id}/complete",
        data={"completed_date": "2026-07-21", "actual_cost": "3500"},
        follow_redirects=True)
    assert complete_resp.status_code == 200
    assert db.session.get(MaintenanceOrder, order.id).status == "COMPLETED"

    print_resp = client.get(f"/transactions/maintenance-orders/{order.id}/print")
    assert print_resp.status_code == 200
    assert b"MAINTENANCE ORDER" in print_resp.data


def test_vehicle_detail_shows_maintenance_history(client, db):
    user = _login(client, db, codes=[
        "vehicle.view", "maintenanceorder.view", "maintenanceorder.create",
        "maintenanceorder.update"])
    branch, vt, mt, vehicle, vendor = _seed_common(db)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date(2026, 7, 1), odometer_at_service=5000, user=user)
    MaintenanceOrderService().submit(order.id, user=user)
    MaintenanceOrderService().start_work(order.id)
    MaintenanceOrderService().complete(order.id, actual_cost=2000,
                                       completed_date=date(2026, 7, 2))

    resp = client.get(f"/master/vehicles/{vehicle.id}")
    assert resp.status_code == 200
    assert order.document_number.encode() in resp.data


# ---------- Tire / Battery Transaction UI ----------

def test_tiretxn_list_and_new_render(client, db):
    _login(client, db, codes=["tiretxn.view", "tiretxn.create"])
    assert client.get("/transactions/tire-transactions").status_code == 200
    assert client.get("/transactions/tire-transactions/new").status_code == 200


def test_tiretxn_create_mounts_tire(client, db):
    user = _login(client, db, codes=["tiretxn.view", "tiretxn.create"])
    branch, vt, mt, vehicle, vendor = _seed_common(db)
    tire = TireService().create(serial_number="TIRE-3B1", brand="Bridgestone",
                                size="265/65R17", tire_type="RADIAL",
                                vendor_id=vendor.id)
    from app.modules.document_config.models import DocumentType
    DocumentTypeService().create(code="TIR", name="Tire Txn",
                                 requires_approval=False, auto_numbering=True)
    dt_tir = DocumentType.query.filter_by(code="TIR").first()
    NumberingSchemeService().create(document_type_id=dt_tir.id, prefix="TIR",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    resp = client.post("/transactions/tire-transactions/new", data={
        "tire_id": str(tire.id), "vehicle_id": str(vehicle.id),
        "action": "MOUNT", "transaction_date": "2026-07-15",
        "odometer_at_service": "1000",
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert TireService().get(tire.id).status == "MOUNTED"


def test_batterytxn_list_and_new_render(client, db):
    _login(client, db, codes=["batterytxn.view", "batterytxn.create"])
    assert client.get("/transactions/battery-transactions").status_code == 200
    assert client.get("/transactions/battery-transactions/new").status_code == 200
