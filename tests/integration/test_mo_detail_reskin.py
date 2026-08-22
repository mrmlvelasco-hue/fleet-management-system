"""Parity net for the Maintenance Order detail re-skin.

Written BEFORE the re-skin, from the template as it stands. The screen
had essentially no coverage: a re-skin could have dropped a whole panel
and nothing would have failed.

That is not hypothetical here. This project has lost content to a
redesign three times -- the vehicle detail screen (6 of 8 sections), the
vehicle form (19 of 62 fields), and the vehicle print view (2 of 4
sections) -- each time with a green suite, because the tests described
what had been built.

So these assert SECTIONS AND ACTIONS, not markup. Classes, grid
structure and ordering are all free to change; what must survive is that
the approver can still see the vehicle, the scope of work, the parts,
the costs, the approval trail, and the buttons that let them act.
"""
from datetime import date

from app.core.security.password import hash_password
from app.modules.document_config.service import (
    DocumentTypeService, NumberingSchemeService)
from app.modules.master_data.org.service import BranchService
from app.modules.master_data.reference.service import VehicleTypeService
from app.modules.master_data.vehicle.service import VehicleService
from app.modules.user_management.models import Permission, Role, User


def _login(client, db, *, codes=()):
    role = Role(name="MODetailSkinRole")
    for code in codes:
        module, action = code.split(".")
        p = Permission.query.filter_by(code=code).first()
        if p is None:
            p = Permission(code=code, module=module, action=action)
            db.session.add(p)
        role.permissions.append(p)
    u = User(username="mo_skin_user", email="mo_skin@x.com",
             password_hash=hash_password("pw123456"))
    u.roles.append(role)
    db.session.add_all([role, u])
    db.session.commit()
    client.post("/login", data={"username": "mo_skin_user",
                                "password": "pw123456"})
    return u


def _order(db, suffix, *, with_parts=False, with_checklist=False):
    from app.modules.document_config.models import DocumentType
    from app.modules.master_data.reference.service import MaintenanceTypeService
    from app.modules.transactions.maintenance_order.models import (
        MaintenanceChecklistItem, MaintenanceOrderPart)
    from app.modules.transactions.maintenance_order.service import (
        MaintenanceOrderService)

    branch = BranchService().create(code=f"BR-{suffix}", name="Skin Branch")
    vt = VehicleTypeService().create(code=f"LV-{suffix}", name="Light",
                                     category="LIGHT")
    mt = MaintenanceTypeService().create(code=f"PM-{suffix}", name="5K PMS",
                                         category="PREVENTIVE")
    vehicle = VehicleService().create(
        vehicle_type_id=vt.id, brand="Isuzu", model="Elf NHR", year=2022,
        branch_id=branch.id, conduction_number=f"CN-{suffix}",
        plate_number=f"ABC-{suffix}", current_odometer=78450)
    if DocumentType.query.filter_by(code="MO").first() is None:
        DocumentTypeService().create(code="MO", name="Maintenance Order",
                                     requires_approval=False,
                                     auto_numbering=True)
        dt = DocumentType.query.filter_by(code="MO").first()
        NumberingSchemeService().create(document_type_id=dt.id, prefix="MO",
                                        include_year=True, digit_count=6,
                                        reset_policy="YEARLY")
    order = MaintenanceOrderService().create(
        vehicle_id=vehicle.id, maintenance_type_id=mt.id,
        scheduled_date=date.today(), user=None)
    order.description = "Scheduled preventive maintenance, 20,000 km."
    order.assigned_mechanic = "M. Aquino"
    order.estimated_cost = 48650
    # The reading taken when the order was raised -- distinct from the
    # vehicle's current odometer, which moves on afterwards.
    order.odometer_at_service = 78450

    if with_parts:
        db.session.add(MaintenanceOrderPart(
            order_id=order.id, part_description="Engine oil",
            specification="15W-40", uom="L", quantity=12,
            estimated_unit_cost=700, sort_order=1))
    if with_checklist:
        db.session.add_all([
            MaintenanceChecklistItem(
                order_id=order.id, activity_code="ENG-01",
                activity_description="Engine oil change", is_done=True,
                sort_order=1),
            MaintenanceChecklistItem(
                order_id=order.id, activity_code="BRK-01",
                activity_description="Brake system inspection",
                is_done=False, sort_order=2),
        ])
    db.session.commit()
    return order


def _page(client, order):
    r = client.get(f"/transactions/maintenance-orders/{order.id}")
    assert r.status_code == 200
    return r.data.decode("utf-8")


# ── Sections that must survive any re-skin ──────────────────────────────────

def test_every_panel_survives(client, db):
    """Each of these is a panel an approver relies on. Losing one to a
    layout change is silent: the page still renders, still looks
    finished, and the missing information is simply not asked for."""
    order = _order(db, "PANEL", with_parts=True, with_checklist=True)
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)

    # Matched WITHOUT ampersands: Jinja escapes them to &amp;, so
    # asserting "Parts & Materials" fails on a panel that is present.
    for section in ("Vehicle Information", "Cost Summary",
                    "Parts", "Materials", "Invoices",
                    "Scope of Work", "Comment"):
        assert section in html, f"panel lost in re-skin: {section}"


def test_the_vehicle_facts_an_approver_checks_are_present(client, db):
    """An approver is deciding whether this spend is justified for THIS
    unit. Plate, make/model, branch and odometer are how they tell."""
    order = _order(db, "VEH")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)

    assert "ABC-VEH" in html
    assert "Isuzu" in html and "Elf NHR" in html
    assert "Skin Branch" in html
    # Odometer is thousands-separated. A bare 78450 on a phone is easy
    # to misread by an order of magnitude, which is the difference
    # between a due service and an overdue one.
    assert "78,450" in html


def test_costs_are_shown(client, db):
    order = _order(db, "COST")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert "Estimated Cost" in html
    assert "48,650" in html


def test_checklist_items_render(client, db):
    order = _order(db, "CHK", with_checklist=True)
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert "Engine oil change" in html
    assert "Brake system inspection" in html


def test_parts_render_with_their_total(client, db):
    order = _order(db, "PRT", with_parts=True)
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert "Engine oil" in html
    assert "8,400.00" in html      # 12 x 700


def test_the_document_number_is_the_page_identity(client, db):
    """On a phone the header is most of what is visible at a glance. If
    the document number is not in it, an approver cannot tell which
    order they are about to approve."""
    order = _order(db, "DOC")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert order.document_number in html


# ── Permission gating must survive too ──────────────────────────────────────

def test_print_is_hidden_without_the_print_permission(client, db):
    """Re-skins lose `{% if %}` wrappers easily -- the markup moves and
    the condition gets dropped with the div it was on. Hiding a control
    is UX only; the route still enforces it. But an approver offered a
    button that then 403s has been told the system is broken."""
    order = _order(db, "NOPRT")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert f"/maintenance-orders/{order.id}/print" not in html


def test_print_is_offered_with_the_permission(client, db):
    order = _order(db, "PRT2")
    _login(client, db, codes=["maintenanceorder.view",
                              "maintenanceorder.print"])
    html = _page(client, order)
    assert f"/maintenance-orders/{order.id}/print" in html


def test_edit_is_hidden_without_update(client, db):
    order = _order(db, "NOEDIT")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert "Submit for Approval" not in html


# ── Mobile: the approver's controls must not be buried ──────────────────────

def test_decision_controls_precede_the_long_panels_in_the_document(client, db):
    """Source order IS mobile order.

    The two-column layout stacks on a phone, and the left column holds
    the parts table, the invoice list and the entire comment thread. An
    approver on a phone therefore had to scroll past all of it to reach
    the buttons they opened the page to press.

    Asserting on POSITION in the document rather than on CSS: a class
    can be renamed, but if the actions render after the comment thread
    then on a phone they are below it, whatever the stylesheet says.
    """
    order = _order(db, "ORDER", with_parts=True, with_checklist=True)
    _login(client, db, codes=["maintenanceorder.view",
                              "maintenanceorder.update"])
    html = _page(client, order)

    actions_at = html.find("Order Actions")
    comments_at = html.find("Post a new comment")
    assert actions_at != -1, "the actions panel is gone"
    assert comments_at != -1, "the comment thread is gone"
    assert actions_at < comments_at, (
        "the approver's controls render after the comment thread, so on a "
        "phone they sit below it")


def test_the_page_declares_a_mobile_viewport(client, db):
    """Without this, a phone renders the desktop layout scaled down and
    every tap target is a third of its intended size."""
    order = _order(db, "VP")
    _login(client, db, codes=["maintenanceorder.view"])
    html = _page(client, order)
    assert "viewport" in html
