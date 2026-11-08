"""A small, generic TTL cache for expensive, read-only computations
that get called repeatedly within a short window.

2026-11-09 performance audit: several dashboard widgets each
independently recompute the exact same "due vehicles" list (PM due,
then again for the Registration-renewal twin) on every request --
measured at ~150-220ms per call at 5,000 vehicles, with no sharing
even within one /dashboard/bootstrap request that triggers several of
them back to back.

The pattern this extracts already existed, inline, as
(_DUE_CACHE, _DUE_LOCK) in app/modules/api/dashboard.py's
_shared_due_rows() -- a 12-second TTL, thread-safe, size-bounded dict.
Pulling it out under one name lets every other caller (the
Registration due-calc, the PM/Registration chart variants that pass
different arguments) reuse the SAME proven pattern instead of each
growing its own copy, matching the project's "reuse generic
components" instruction. It is deliberately ignorant of *who* is
calling or *what* is being computed -- the caller supplies the key
and the compute function, so this carries no business logic of its
own to drift from the real thing.

Not a fit for anything that must reflect a write immediately -- a 12s
(or caller-chosen) staleness window is fine for a dashboard tile a
person is glancing at, not for a page that just submitted a change and
needs to see its own result.
"""
import functools
import threading
import time


class TTLCache:
    def __init__(self, ttl_seconds: float, max_entries: int = 64):
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._store = {}  # key -> (expires_at, value)

    def get_or_compute(self, key, compute):
        """Return the cached value for `key` if it hasn't expired,
        otherwise call `compute()` (with no arguments -- the caller
        closes over whatever it needs), store the result, and return
        it."""
        now = time.time()
        with self._lock:
            hit = self._store.get(key)
            if hit and hit[0] > now:
                return hit[1]

        value = compute()

        with self._lock:
            self._store[key] = (now + self._ttl, value)
            if len(self._store) > self._max_entries:
                expired = [k for k, (exp, _) in self._store.items()
                          if exp <= now]
                for k in expired:
                    self._store.pop(k, None)
        return value

    def invalidate(self, key=None):
        """Drop one key, or everything when key is None -- for a write
        path that must not let a later read see a value cached before
        the write (same reasoning as
        UserOrgScopeService.invalidate_cache)."""
        with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


def ttl_cached(ttl_seconds: float, key_func, max_entries: int = 64):
    """Method decorator wrapping a widget/orchestration method (NOT a
    low-level due-calc method -- see this module's docstring) in a
    dedicated TTLCache instance.

    `key_func(*args, **kwargs)` builds the cache key from the same
    arguments the wrapped method receives (`self` included, since it's
    cheap to hash and makes per-instance caching the default rather
    than something the caller has to opt into).

    The TTLCache instance is exposed as `<wrapped method>.cache`, so a
    test or a write path can call `Service.some_method.cache.invalidate()`
    directly -- matching the existing `UserOrgScopeService.invalidate_cache`
    pattern elsewhere in this codebase, rather than inventing a second
    way to bust a cache.
    """
    def decorator(fn):
        cache = TTLCache(ttl_seconds=ttl_seconds, max_entries=max_entries)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_func(*args, **kwargs)
            return cache.get_or_compute(key, lambda: fn(*args, **kwargs))

        wrapper.cache = cache
        return wrapper
    return decorator
