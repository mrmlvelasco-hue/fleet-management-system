"""TTLCache: the generic helper extracted from dashboard.py's inline
(_DUE_CACHE, _DUE_LOCK) pattern (2026-11-09 performance audit)."""
import time

from app.core.ttl_cache import TTLCache


def test_a_fresh_key_computes():
    cache = TTLCache(ttl_seconds=60)
    calls = []
    result = cache.get_or_compute("k", lambda: calls.append(1) or "value")
    assert result == "value"
    assert len(calls) == 1


def test_a_cached_key_does_not_recompute():
    cache = TTLCache(ttl_seconds=60)
    calls = []
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    assert len(calls) == 1


def test_an_expired_key_recomputes():
    cache = TTLCache(ttl_seconds=0.01)
    calls = []
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    time.sleep(0.02)
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    assert len(calls) == 2


def test_different_keys_are_independent():
    cache = TTLCache(ttl_seconds=60)
    calls = []
    a = cache.get_or_compute("a", lambda: calls.append("a") or "A")
    b = cache.get_or_compute("b", lambda: calls.append("b") or "B")
    assert (a, b) == ("A", "B")
    assert calls == ["a", "b"]


def test_invalidate_one_key():
    cache = TTLCache(ttl_seconds=60)
    calls = []
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    cache.invalidate("k")
    cache.get_or_compute("k", lambda: calls.append(1) or "value")
    assert len(calls) == 2


def test_invalidate_everything():
    cache = TTLCache(ttl_seconds=60)
    calls = []
    cache.get_or_compute("a", lambda: calls.append(1) or "A")
    cache.get_or_compute("b", lambda: calls.append(1) or "B")
    cache.invalidate()
    cache.get_or_compute("a", lambda: calls.append(1) or "A")
    cache.get_or_compute("b", lambda: calls.append(1) or "B")
    assert len(calls) == 4


def test_bounded_size_drops_only_expired_entries():
    cache = TTLCache(ttl_seconds=0.01, max_entries=2)
    cache.get_or_compute("a", lambda: "A")
    time.sleep(0.02)
    cache.get_or_compute("b", lambda: "B")
    cache.get_or_compute("c", lambda: "C")
    # "a" was expired at insert time of the entry that pushed the store
    # past max_entries, and should have been swept.
    assert "a" not in cache._store
