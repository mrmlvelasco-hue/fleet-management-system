"""The eight transaction lists, and two app-wide UI defects.

Covers the standard filter bar reaching every transaction list, the
Created Date column and filter option on each, and two CSS/JS problems
that made whole screens misbehave:

  * page headers pushing their action buttons into the middle of the
    page once an Export button appeared;
  * row action menus being clipped, so a one-row list appeared to offer
    only its first action.
"""
import pytest
from pathlib import Path

from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


@pytest.fixture()
def admin(app, db):
    from app.modules.user_management.models import User
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    return User.query.filter_by(username="admin").first()


def _login(client):
    return client.post("/login", data={"username": "admin",
                                       "password": "Testpass123!"},
                       follow_redirects=True)


LISTS = [
    ("/transactions/trip-tickets", "departure_datetime"),
    ("/transactions/atd", "valid_from"),
    ("/transactions/vehicle-movements", "movement_date"),
    ("/transactions/tire-transactions", "transaction_date"),
    ("/transactions/battery-transactions", "transaction_date"),
    ("/transactions/purchase-requests", "created_at"),
    ("/transactions/vehicle-registrations", "registration_date"),
    ("/transactions/maintenance-orders", "scheduled_date"),
]


@pytest.mark.parametrize("path, default_date_field", LISTS)
def test_every_transaction_list_has_the_standard_filter_bar(
        app, client, db, admin, path, default_date_field):
    _login(client)
    html = client.get(path).get_data(as_text=True)
    assert 'name="q"' in html, f"{path} has no search box"
    assert 'name="date_field"' in html, f"{path} has no date-type picker"
    assert f'value="{default_date_field}"' in html


@pytest.mark.parametrize("path, _d", LISTS)
def test_every_transaction_list_offers_created_date(app, client, db, admin,
                                                    path, _d):
    """Created Date must be filterable everywhere -- on the lists that
    display no created date of their own it is the only way to ask what
    was ENTERED in a period."""
    _login(client)
    assert 'value="created_at"' in client.get(path).get_data(as_text=True)


@pytest.mark.parametrize("path, _d", LISTS)
def test_every_transaction_list_scrolls_sideways_on_mobile(
        app, client, db, admin, path, _d):
    """Without .table-responsive a nine-column table widens the whole
    document: measured layout viewports of 740-1017px against a 390px
    device, which makes the browser render everything zoomed out and
    pushes fixed overlays off-screen."""
    _login(client)
    assert "table-responsive" in client.get(path).get_data(as_text=True), (
        f"{path} table is not wrapped for horizontal scrolling")


@pytest.mark.parametrize("path, _d", LISTS)
def test_no_transaction_list_still_uses_client_side_datatables(
        app, client, db, admin, path, _d):
    """These lists paginate server-side now. A DataTables search box on
    top of that only ever matches the rows already on screen, which is
    worse than no search at all because it looks like it searches
    everything."""
    _login(client)
    assert "fms-datatable" not in client.get(path).get_data(as_text=True)


# ── The two app-wide UI defects ─────────────────────────────────────

def test_page_header_actions_stay_together_on_the_right(app):
    """A page header is `d-flex justify-content-between` with the title
    then the actions. That works with two children; the moment an Export
    button made it three, space-between distributed the free space
    BETWEEN all three and the New/More Actions group drifted into the
    middle of the page. Roughly twenty list screens were affected.

    Auto margins resolve before justify-content, so putting margin-left
    auto on the element after the title packs everything after it to the
    right without restructuring twenty templates.
    """
    css = Path(app.root_path, "static", "css", "theme.css").read_text()
    assert ".d-flex.justify-content-between > h1 + *" in css


def test_tables_do_not_clip_their_row_action_menus(app):
    """theme-enterprise.css carries `.table { border-radius: 14px;
    overflow: hidden; }`. The overflow exists only so the radius clips
    the corner cells, but it clipped every row action menu in the app --
    and on a one-row list the table is barely taller than the row, so
    everything below the first menu item vanished. That is why a
    single-result vehicle list appeared to offer only "View Details":
    Edit was rendered, just clipped.

    Rounding the corner CELLS instead gives the same visual with no
    clipping, so this is fixed at the source rather than per screen.
    """
    css = Path(app.root_path, "static", "css", "theme.css").read_text()
    assert "table.table {" in css
    assert "overflow: visible" in css
    assert "border-top-left-radius: 14px" in css


def test_open_row_menu_escapes_the_scroll_container(app):
    """.table-responsive must stop clipping while a menu is open --
    handled in JS rather than with :has(), which silently does nothing
    in older browsers."""
    css = Path(app.root_path, "static", "css", "theme.css").read_text()
    js = Path(app.root_path, "static", "js", "app.js").read_text()
    assert ".table-responsive.has-open-dropdown" in css
    assert "show.bs.dropdown" in js
    assert "hide.bs.dropdown" in js
