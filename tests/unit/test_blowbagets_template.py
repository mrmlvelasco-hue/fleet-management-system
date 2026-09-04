"""The BLOWBAGETS pre-trip inspection, seeded.

The client's standard, and a real one: drivers are taught the mnemonic,
so a checklist that asks the questions in a different order fights the
training rather than following it.

No template was seeded at all before this. The "Daily Pre-Trip
Inspection" on the device was hand-built, so its order was whatever
somebody happened to type -- the same failure as the roles, where the
right answer lived in a person's memory instead of the system.

Order is DATA (category.sort_order), not a hard-coded sort. Fleet can
add an item, retire one, or reorder for a particular vehicle type
without a code change, which is what the master prompt means by "no
values shall be hardcoded".
"""
import pytest

from app.cli import BLOWBAGETS, _seed_checklist_templates
from app.extensions import db
from app.modules.transactions.vehicle_checklist.models import (
    ChecklistCategory, ChecklistItem, ChecklistTemplate)

TEMPLATE = "Daily Pre-Trip Inspection (BLOWBAGETS)"


@pytest.fixture()
def seeded(app):
    _seed_checklist_templates()
    db.session.commit()


def _categories():
    t = ChecklistTemplate.query.filter_by(name=TEMPLATE).first()
    assert t is not None, "template was not seeded"
    return (ChecklistCategory.query.filter_by(template_id=t.id)
            .order_by(ChecklistCategory.sort_order).all())


def test_the_template_is_seeded(app, seeded):
    assert ChecklistTemplate.query.filter_by(name=TEMPLATE).first()


def test_the_ten_letters_are_in_mnemonic_order(app, seeded):
    """B-L-O-W-B-A-G-E-T-S. The order IS the mnemonic; a driver reciting
    it while working down the screen must not have to jump around."""
    assert [c.name.split(" ")[0] for c in _categories()] == [
        "B", "L", "O", "W", "B", "A", "G", "E", "T", "S"]


def test_the_two_Bs_are_distinct(app, seeded):
    """Battery and Brakes both start with B, and collapsing them is the
    obvious way to get this wrong."""
    names = [c.name for c in _categories()]
    assert any("Battery" in n for n in names)
    assert any("Brakes" in n for n in names)
    assert len(set(names)) == 10


def test_sort_order_is_stored_not_implied(app, seeded):
    """Ordering must survive a query that does not sort by insertion
    id -- and must be editable by Fleet without a code change."""
    orders = [c.sort_order for c in _categories()]
    assert orders == sorted(orders)
    assert len(set(orders)) == 10


def test_every_category_has_at_least_one_item(app, seeded):
    for c in _categories():
        assert ChecklistItem.query.filter_by(category_id=c.id).count() >= 1


def test_safety_critical_categories_are_flagged(app, seeded):
    """Brakes, Lights, Tires and Self. is_safety drives the scoring and
    the defect path, so a failure there is not just another red mark."""
    flagged = set()
    for c in _categories():
        for i in ChecklistItem.query.filter_by(category_id=c.id):
            if i.is_safety:
                flagged.add(c.name.split("— ")[-1])
    assert {"Brakes", "Lights", "Tires", "Self"} <= flagged


def test_self_check_is_last_and_is_about_the_driver(app, seeded):
    """S is the driver, not the vehicle -- the one item on the list that
    checks the person operating it, and the reason it ends the
    sequence."""
    last = _categories()[-1]
    assert last.name.startswith("S")
    assert "Self" in last.name


def test_seeding_twice_does_not_duplicate(app, seeded):
    _seed_checklist_templates()
    db.session.commit()
    assert ChecklistTemplate.query.filter_by(name=TEMPLATE).count() == 1


def test_seeding_never_overwrites_an_edited_template(app, seeded):
    """Fleet tailoring the list must not have their work reverted the
    next time somebody runs seed. Seeding creates; it does not
    reconcile."""
    cat = _categories()[0]
    db.session.add(ChecklistItem(category_id=cat.id, name="Site-specific check",
                                 sort_order=99))
    db.session.commit()

    _seed_checklist_templates()
    db.session.commit()

    assert ChecklistItem.query.filter_by(name="Site-specific check").count() == 1


def test_the_constant_matches_the_mnemonic(app):
    """Guards the source data itself, so a typo in the table cannot
    silently reorder a driver's morning."""
    assert "".join(letter for letter, _n, _items in BLOWBAGETS) == "BLOWBAGETS"
