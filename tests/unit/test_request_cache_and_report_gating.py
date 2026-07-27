"""Tests for the dashboard duplicate-work fix and the PMS Compliance
report's parameter gate.
"""
import pytest

from app.core.request_cache import request_cached


def test_repeated_calls_run_once_within_a_request(app):
    calls = {"n": 0}

    @request_cached("t_once")
    def expensive():
        calls["n"] += 1
        return "result"

    with app.test_request_context("/"):
        assert expensive() == "result"
        assert expensive() == "result"
        assert expensive() == "result"
    assert calls["n"] == 1, "the expensive call must run once per request"


def test_each_request_recomputes(app):
    """Results must NOT leak across requests -- a completed order or a
    new odometer reading changes the answer, and a stale due list would
    be worse than a slow one.

    Asserted through REAL requests rather than nested
    test_request_context() blocks: `g` lives on the APPLICATION context,
    and the test fixture pre-pushes one, so synthetic request contexts
    inside it would share `g` and wrongly look like a leak. Flask pushes
    a fresh application context per real request, which is what actually
    governs the cache lifetime in production.
    """
    calls = {"n": 0}

    @request_cached("t_per_request")
    def expensive():
        calls["n"] += 1
        return calls["n"]

    @app.route("/__cache_probe")
    def _probe():
        expensive()
        expensive()   # twice within ONE request
        return "ok"

    client = app.test_client()
    client.get("/__cache_probe")
    assert calls["n"] == 1, "must be deduplicated within a single request"
    client.get("/__cache_probe")
    assert calls["n"] == 2, "must recompute on the next request, not serve stale"


def test_different_arguments_cached_separately(app):
    @request_cached("t_args")
    def double(_self, n):
        return n * 2

    with app.test_request_context("/"):
        assert double(None, 2) == 4
        assert double(None, 5) == 10


def test_no_request_context_falls_through(app):
    """CLI commands and Celery tasks run outside a request -- they must
    still work, just uncached."""
    calls = {"n": 0}

    @request_cached("t_nocontext")
    def expensive():
        calls["n"] += 1
        return "ok"

    with app.app_context():
        assert expensive() == "ok"
        assert expensive() == "ok"
    assert calls["n"] == 2  # no caching, but no crash either


def test_unhashable_arguments_do_not_crash(app):
    @request_cached("t_unhashable")
    def takes_list(_self, items):
        return len(items)

    with app.test_request_context("/"):
        assert takes_list(None, [1, 2, 3]) == 3
