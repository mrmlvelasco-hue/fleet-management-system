"""Dashboard panel structure: no duplicate headers, no nested cards.

Reported: "Vehicles Due for Registration Renewal" appeared TWICE, one
inside the other, because the async partial carried its own full card
and header while being injected into a panel that already had one. The
inner card's .table-responsive also introduced a horizontal scrollbar.
"""
from app.core.security.registry import sync_permissions
from app.cli import _seed_admin


def _client(app, db):
    sync_permissions()
    db.session.commit()
    _seed_admin("Testpass123!")
    c = app.test_client()
    c.post("/login", data={"username": "admin",
                           "password": "Testpass123!"},
          follow_redirects=True)
    return c


def test_registration_title_appears_exactly_once(app, db):
    client = _client(app, db)
    html = client.get("/").get_data(as_text=True)
    assert html.count("Vehicles Due for Registration Renewal") == 1


def test_injected_partial_carries_no_card_or_header(app, db):
    """The panel owns the chrome; the partial owns the rows. If the
    partial regains a card-header, the duplicate title returns."""
    import json
    client = _client(app, db)
    data = json.loads(
        client.get("/dashboard/widgets").get_data(as_text=True))
    partial = data["due_registration_html"]
    assert "card-header" not in partial
    assert "Vehicles Due for Registration Renewal" not in partial
    # .table-responsive is what produced the horizontal scrollbar.
    assert "table-responsive" not in partial


def test_hero_renders_above_the_breadcrumb(app, db):
    """Requested: the banner should sit directly under the top bar."""
    client = _client(app, db)
    html = client.get("/").get_data(as_text=True)
    assert html.index("ENTERPRISE FLEET MANAGEMENT SYSTEM") < html.index("breadcrumb")
