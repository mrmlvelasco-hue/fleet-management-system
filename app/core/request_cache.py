"""Request-scoped memoisation for expensive, read-only calculations.

The dashboard was calling the SAME heavy due-calculation twice per page
load -- once for the KPI card's count and again to build the list right
underneath it -- for both Maintenance and Registration. Four full passes
over the fleet where one each would do.

Each pass rebuilds an in-memory snapshot of every active PM schedule
(4,600+ rows on a fully-imported catalogue) and then matches every
vehicle against it, so the duplication is not a rounding error: it is
literally double the most expensive thing the page does.

Flask's `g` is the right scope here. These results must NOT be cached
across requests -- a vehicle's odometer or a completed order changes the
answer, and showing yesterday's due list would be worse than showing it
slowly. `g` is torn down at the end of every request, so each page load
recomputes exactly once and callers stay free to call as often as they
like.

Outside a request context (CLI commands, Celery tasks, tests) it falls
straight through to the real function with no caching, so behaviour is
identical there.
"""
import functools

from flask import g, has_request_context


def request_cached(key: str):
    """Memoise a zero-argument-ish call for the life of one request.

    `key` must be unique per logical result. Arguments are folded into
    the cache key so that, e.g., a per-user scoped call and an unscoped
    call don't collide.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not has_request_context():
                return fn(*args, **kwargs)

            # Skip `self` when building the key: two instances of a
            # stateless service should share a cached result, and
            # including the instance would defeat the whole point since
            # the callers construct a fresh service each time.
            key_args = args[1:] if args else ()
            try:
                cache_key = (key, key_args,
                            tuple(sorted(kwargs.items())))
                hash(cache_key)
            except TypeError:
                # An unhashable argument (a list of ids, say) -- don't
                # guess at an identity, just don't cache this call.
                return fn(*args, **kwargs)

            store = getattr(g, "_fms_request_cache", None)
            # `g` lives on the APPLICATION context, not the request, and
            # those are not always one-to-one: anything holding a single
            # app context open while handling several logical operations
            # (a Celery worker, a long-running script, the test fixtures)
            # would otherwise keep serving the first result forever.
            # Stamping the store with the identity of the request it was
            # built for makes it reset the moment a different request is
            # being served, so "request-scoped" is literally true rather
            # than true-by-convention.
            from flask import request
            request_id = id(request._get_current_object())
            if store is None or store.get("__request_id__") != request_id:
                store = {"__request_id__": request_id}
                g._fms_request_cache = store
            if cache_key not in store:
                store[cache_key] = fn(*args, **kwargs)
            return store[cache_key]
        return wrapper
    return decorator
