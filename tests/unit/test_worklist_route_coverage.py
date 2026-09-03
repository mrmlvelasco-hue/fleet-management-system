"""Every approvable document must resolve to a React screen, or be
explicitly declared as not having one yet.

Written after a client screenshot showed a "For Your Action" list where
Trip Ticket and ATD rows were plain text while Maintenance Order rows
were links. Nothing was broken -- _REACT_ROUTE_MAP simply had two
entries, and the screens for the rest had shipped since.

This is the SECOND time this map has drifted. The first cost two
commits during which a built Purchase Request screen was unreachable.
The comment in worklist.py already says a map maintained separately
from the screens it points at will drift and that nothing enforces the
two stay in sync "except a human noticing".

This test is that enforcement. It does not know what React ships -- it
cannot -- so instead it forces a DECISION: every approvable table is
either mapped, or listed in _NO_REACT_SCREEN with a reason. A new module
that ships without being classified fails here rather than silently
rendering as unclickable text on someone's phone.
"""
import pytest

from app.modules.api.worklist import _REACT_ROUTE_MAP, _NO_REACT_SCREEN

#: Every table that can carry an approval workflow. Adding a module to
#: the approval engine means adding it here too -- and then the test
#: below forces you to say whether it has a React screen.
APPROVABLE_TABLES = {
    "maintenance_orders",
    "purchase_requests",
    "trip_tickets",
    "authority_to_drives",
    "vehicle_movements",
    "vehicle_registrations",
    "tire_transactions",
    "battery_transactions",
    "maintenance_invoices",
    "vehicle_checklists",
}


def test_every_approvable_table_is_classified():
    """Mapped, or explicitly declared screenless. Never just absent."""
    classified = set(_REACT_ROUTE_MAP) | set(_NO_REACT_SCREEN)
    unclassified = APPROVABLE_TABLES - classified
    assert not unclassified, (
        f"These tables resolve to no URL and are not declared screenless: "
        f"{sorted(unclassified)}. A worklist row for them renders as "
        f"plain text an approver cannot click.")


def test_the_two_sets_do_not_overlap():
    """A table declared screenless AND mapped means one of the two is
    stale, and which one wins is an accident of dictionary lookup."""
    assert not (set(_REACT_ROUTE_MAP) & set(_NO_REACT_SCREEN))


def test_no_stray_entries():
    """An entry for a table that cannot be approved is dead code that
    reads as coverage."""
    stray = (set(_REACT_ROUTE_MAP) | set(_NO_REACT_SCREEN)) - APPROVABLE_TABLES
    assert not stray, f"Unknown tables in the route maps: {sorted(stray)}"


@pytest.mark.parametrize("table", sorted(_REACT_ROUTE_MAP))
def test_each_template_takes_an_id(table):
    """A template missing {id} would send every approver of that type to
    the same record."""
    assert "{id}" in _REACT_ROUTE_MAP[table]
    assert _REACT_ROUTE_MAP[table].startswith("/")


@pytest.mark.parametrize("table", [
    "trip_tickets", "authority_to_drives", "vehicle_movements",
    "vehicle_registrations", "tire_transactions", "battery_transactions",
])
def test_the_screens_the_client_reported_now_resolve(table):
    """The specific gap in the screenshot: Trip Ticket and ATD rows were
    unclickable while Maintenance Order rows were links."""
    assert table in _REACT_ROUTE_MAP
