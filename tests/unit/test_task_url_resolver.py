import pytest


class _FakeTask:
    def __init__(self, reference_table, reference_id):
        self.reference_table = reference_table
        self.reference_id = reference_id


def test_resolve_trip_ticket_url(app):
    from app.core.approval.task_url_resolver import resolve_task_url
    with app.test_request_context():
        url = resolve_task_url(_FakeTask("trip_tickets", 42))
        assert url == "/transactions/trip-tickets/42"


def test_resolve_maintenance_order_url(app):
    from app.core.approval.task_url_resolver import resolve_task_url
    with app.test_request_context():
        url = resolve_task_url(_FakeTask("maintenance_orders", 7))
        assert url == "/transactions/maintenance-orders/7"


def test_resolve_unknown_table_returns_none(app):
    from app.core.approval.task_url_resolver import resolve_task_url
    with app.test_request_context():
        assert resolve_task_url(_FakeTask("some_future_module", 1)) is None
