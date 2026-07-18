from datetime import date

from app.core.security.password import hash_password
from app.modules.user_management.models import User, Role, Permission
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.master_data.reference.service import (
    VehicleTypeService, MaintenanceTypeService)
from app.modules.master_data.org.service import BranchService
from app.modules.transactions.maintenance_order.service import (
    TransactionTypeService)
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)


def _login(client, db, *, codes=()):
    role = Role(name="MOCategoryUIRole")
    for code in codes:
        m, a = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=m, action=a)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_category_ui_user", email="mo_category_ui_user@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_category_ui_user", "password": "pw123456"})
    return u


def test_mo_form_shows_category_selector_and_grouped_transaction_types(client, db):
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    TransactionTypeService().create(code="DEP-TEST", name="Assignment",
                                    order_category="OPERATIONAL", group="DEPLOYMENT")
    TransactionTypeService().create(code="ADM-TEST", name="Change of Ownership",
                                    order_category="OPERATIONAL", group="ADMINISTRATIVE")
    TransactionTypeService().create(code="DIS-TEST", name="Scrappage",
                                    order_category="OPERATIONAL", group="DISPOSAL")
    TransactionTypeService().create(code="ACC-TEST", name="Application",
                                    order_category="OPERATIONAL", group="ACCESSORIES")

    resp = client.get("/transactions/maintenance-orders/new")
    assert resp.status_code == 200
    assert b'id="moOrderCategorySelect"' in resp.data
    assert b"Deployment" in resp.data
    assert b"Administrative" in resp.data
    assert b"Disposal" in resp.data
    assert b"Accessories" in resp.data
    assert b"Scrappage" in resp.data


def test_create_operational_order_without_maintenance_type(client, db):
    """The exact rule requested: an Operational order must submit
    successfully without ever specifying a Maintenance Type or PM Scope
    Template."""
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    branch = BranchService().create(code="BR-MOCATUI", name="MO Category UI Branch")
    vt = VehicleTypeService().create(code="LV-MOCATUI", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOCATUI-000")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")
    deployment_tt = TransactionTypeService().create(
        code="DEP-TEST2", name="Assignment", order_category="OPERATIONAL",
        group="DEPLOYMENT")

    resp = client.post("/transactions/maintenance-orders/new", data={
        "vehicle_id": str(vehicle.id), "order_category": "OPERATIONAL",
        "transaction_type_id": str(deployment_tt.id),
        "scheduled_date": date.today().isoformat(),
        "description": "Vehicle reassignment to new branch",
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert order is not None
    assert order.order_category == "OPERATIONAL"
    assert order.maintenance_type_id is None
    assert order.scope_template_id is None
    assert order.transaction_type_id == deployment_tt.id


def test_maintenance_category_order_still_works_exactly_as_before(client, db):
    """Zero-regression check: the default (Maintenance-category) flow
    must behave identically to before this feature was added."""
    _login(client, db, codes=["maintenanceorder.view", "maintenanceorder.create"])
    branch = BranchService().create(code="BR-MOCATUI2", name="MO Category UI Branch 2")
    vt = VehicleTypeService().create(code="LV-MOCATUI2", name="Light", category="LIGHT")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Toyota", model="Hilux", year=2024,
        branch_id=branch.id, conduction_number="MOCATUI2-000")
    mt = MaintenanceTypeService().create(code="MOCATUI2-MT", name="MO Category UI Test",
                                         category="PM")
    DocumentTypeService().create(code="MO", name="Maintenance Order",
                                 requires_approval=False, auto_numbering=True)
    from app.modules.document_config.models import DocumentType
    dt = DocumentType.query.filter_by(code="MO").first()
    NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                    include_year=True, digit_count=6,
                                    reset_policy="YEARLY")

    resp = client.post("/transactions/maintenance-orders/new", data={
        "vehicle_id": str(vehicle.id), "order_category": "MAINTENANCE",
        "maintenance_type_id": str(mt.id),
        "scheduled_date": date.today().isoformat(),
    }, follow_redirects=True)
    assert resp.status_code == 200

    from app.modules.transactions.maintenance_order.models import MaintenanceOrder
    order = MaintenanceOrder.query.filter_by(vehicle_id=vehicle.id).first()
    assert order is not None
    assert order.order_category == "MAINTENANCE"
    assert order.maintenance_type_id == mt.id
